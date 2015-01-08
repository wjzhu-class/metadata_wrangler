import datetime
import requests

from nose.tools import set_trace
from sqlalchemy import or_
from sqlalchemy.sql.functions import func

from core.monitor import Monitor
from core.model import (
    DataSource,
    Identifier,
    UnresolvedIdentifier,
    Work,
)
from core.opds_import import DetailedOPDSImporter

from appeal import AppealCalculator
from gutenberg import OCLCMonitorForGutenberg
from amazon import AmazonCoverageProvider
from oclc import LinkedDataCoverageProvider
from viaf import VIAFClient

class IdentifierResolutionMonitor(Monitor):
    """Turn an UnresolvedIdentifier into an Edition with a LicensePool."""

    LICENSE_SOURCE_RETURNED_ERROR = "Underlying license source returned error."
    LICENSE_SOURCE_RETURNED_WRONG_CONTENT_TYPE = (
        "Underlying license source served unhandlable media type (%s).")
    LICENSE_SOURCE_NOT_ACCESSIBLE = (
        "Could not access underlying license source over the network.")
    UNKNOWN_FAILURE = "Unknown failure."

    def __init__(self, content_server, overdrive_api, threem_api):
        super(IdentifierResolutionMonitor, self).__init__(
            "Identifier Resolution Manager")
        self.content_server = content_server
        self.overdrive = overdrive_api
        self.threem = threem_api

    def run_once(self, _db, start, cutoff):
        now = datetime.datetime.utcnow()
        one_day_ago = now - datetime.timedelta(days=1)
        needs_processing = or_(
            UnresolvedIdentifier.exception==None,
            UnresolvedIdentifier.most_recent_attempt < one_day_ago)


        batches = 0
        for identifier, handler in (
                [Identifier.GUTENBERG_ID, self.resolve_content_server],
        ):
            q = _db.query(UnresolvedIdentifier).join(
                UnresolvedIdentifier.identifier).filter(
                    Identifier.type==Identifier.GUTENBERG_ID).filter(
                        needs_processing)
            while q.count() and batches < 10:
                batches += 1
                unresolved_identifiers = q.order_by(func.random()).limit(10).all()
                successes, failures = handler(_db, unresolved_identifiers)
                if isinstance(successes, int):
                    # There was a problem getting any information at all from
                    # the server.
                    if successes / 100 == 5:
                        # A 5xx error means we probably won't get any
                        # other information from the server for a
                        # while. Give up on this server for now.
                        break

                    # Some other kind of error means we might have
                    # better luck if we choose different identifiers,
                    # so keep going.
                    successes = failures = []
                    
                for s in successes:
                    _db.delete(s)
                for f in failures:
                    if not f.exception:
                        f.exception = self.UNKNOWN_FAILURE
                    f.most_recent_attempt = now
                    if not f.first_attempt:
                        f.first_attempt = now
                _db.commit()

    def resolve_content_server(self, _db, batch):
        successes = []
        failures = []
        tasks_by_identifier = dict()
        for task in batch:
            tasks_by_identifier[task.identifier] = task
        try:
            response = self.content_server.lookup(
                [x.identifier for x in batch])
        except requests.exceptions.ConnectionError:
            return 500, self.LICENSE_SOURCE_NOT_ACCESSIBLE

        if response.status_code != 200:
            return response.status_code, self.LICENSE_SOURCE_RETURNED_ERROR

        content_type = response.headers['content-type']
        if not content_type.startswith("application/atom+xml"):
            return 500, self.LICENSE_SOURCE_RETURNED_WRONG_CONTENT_TYPE % (
                content_type)

        # We got an OPDS feed. Import it.
        importer = DetailedOPDSImporter(_db, response.text)
        editions, messages = importer.import_from_feed()
        for edition in editions:
            identifier = edition.primary_identifier
            if identifier in tasks_by_identifier:
                successes.append(tasks_by_identifier[identifier])
        for identifier, (status_code, exception) in messages.items():
            if identifier not in tasks_by_identifier:
                # The server sent us a message about an identifier we
                # didn't ask for. No thanks.
                continue
            if status_code / 100 == 2:
                # The server sent us a 2xx status code for this
                # identifier but didn't actually give us any
                # information. That's a server-side problem.
                status_code == 500
            task = tasks_by_identifier[identifier]
            task.status_code = status_code
            task.exception = exception
            failures.append(task)
        return successes, failures
        

class MakePresentationReadyMonitor(Monitor):
    """Make works presentation ready.

    This is an EXTREMELY complicated process, but all the work can be
    delegated to other bits of code.
    """

    def __init__(self, data_directory):
        super(MakePresentationReadyMonitor, self).__init__(
            "Make Works Presentation Ready")
        self.data_directory = data_directory

    def run_once(self, _db, start, cutoff):

        appeal_calculator = AppealCalculator(_db, self.data_directory)

        coverage_providers = dict(
            oclc_gutenberg = OCLCMonitorForGutenberg(_db),
            oclc_linked_data = LinkedDataCoverageProvider(_db),
            amazon = AmazonCoverageProvider(_db),
        )
        unready_works = _db.query(Work).filter(
            Work.presentation_ready==False).filter(
                Work.presentation_ready_exception==None).order_by(
                    Work.last_update_time.desc()).limit(10)
        while unready_works.count():
            for work in unready_works.all():
                self.make_work_ready(_db, work, appeal_calculator, 
                                     coverage_providers)
                # try:
                #     self.make_work_ready(_db, work, appeal_calculator,
                #                          coverage_providers)
                #     work.presentation_ready = True
                # except Exception, e:
                #     work.presentation_ready_exception = str(e)
                _db.commit()

    def make_work_ready(self, _db, work, appeal_calculator, coverage_providers):
        """Either make a work presentation ready, or raise an exception
        explaining why that's not possible.
        """
        did_oclc_lookup = False
        for edition in work.editions:
            # OCLC Lookup on all Gutenberg editions.
            if edition.data_source.name==DataSource.GUTENBERG:
                coverage_providers['oclc_gutenberg'].ensure_coverage(edition)
                did_oclc_lookup = True

        primary_edition = work.primary_edition
        if did_oclc_lookup:
            oclc_ids = primary_edition.equivalent_identifiers(
                type=[Identifier.OCLC_WORK, Identifier.OCLC_NUMBER])
            for o in oclc_ids:
                coverage_providers['oclc_linked_data'].ensure_coverage(o)

        # OCLC Linked Data on all ISBNs. Amazon on all ISBNs + ASINs.
        # equivalent_identifiers = primary_edition.equivalent_identifiers(
        #     type=[Identifier.ASIN, Identifier.ISBN])
        # for identifier in equivalent_identifiers:
        #     coverage_providers['amazon'].ensure_coverage(identifier)
        #     if identifier.type==Identifier.ISBN:
        #         coverage_providers['oclc_linked_data'].ensure_coverage(
        #             identifier)

        # VIAF on all contributors.
        viaf = VIAFClient(_db)
        for edition in work.editions:
            for contributor in primary_edition.contributors:
                viaf.process_contributor(contributor)

        # Calculate appeal. This will obtain Amazon reviews as a side effect.
        appeal_calculator.calculate_for_work(work)

        # Calculate presentation.
        work.calculate_presentation()

        # All done!
        work.set_presentation_ready()