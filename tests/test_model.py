import datetime
import os
import sys
import site
import re

from nose.tools import (
    assert_raises_regexp,
    eq_,
    set_trace,
)

from model import (
    Contributor,
    CirculationEvent,
    DataSource,
    get_one_or_create,
    Work,
    LicensePool,
    WorkIdentifier,
    WorkRecord,
)

from tests.db import (
    setup_module, 
    teardown_module, 
    DatabaseTest,
)

class TestDataSource(DatabaseTest):

    def test_initial_data_sources(self):
        sources = [
            (x.name, x.offers_licenses, x.primary_identifier_type)
            for x in DataSource.well_known_sources(self._db)
        ]

        expect = [
            (DataSource.GUTENBERG, True, WorkIdentifier.GUTENBERG_ID),
            (DataSource.OVERDRIVE, True, WorkIdentifier.OVERDRIVE_ID),
            (DataSource.THREEM, True, WorkIdentifier.THREEM_ID),
            (DataSource.OPEN_LIBRARY, False, WorkIdentifier.OPEN_LIBRARY_ID),
            (DataSource.AXIS_360, True, WorkIdentifier.AXIS_360_ID),
            (DataSource.OCLC, False, WorkIdentifier.OCLC_NUMBER),
            (DataSource.OCLC_LINKED_DATA, False, WorkIdentifier.OCLC_NUMBER),
            (DataSource.WEB, True, WorkIdentifier.URI),
            (DataSource.MANUAL, False, None)
        ]
        eq_(set(sources), set(expect))

class TestWorkIdentifier(DatabaseTest):

    def test_for_foreign_id(self):
        identifier_type = WorkIdentifier.ISBN
        isbn = "3293000061"

        # Getting the data automatically creates a database record.
        identifier, was_new = WorkIdentifier.for_foreign_id(
            self._db, identifier_type, isbn)
        eq_(WorkIdentifier.ISBN, identifier.type)
        eq_(isbn, identifier.identifier)
        eq_(True, was_new)

        # If we get it again we get the same data, but it's no longer new.
        identifier2, was_new = WorkIdentifier.for_foreign_id(
            self._db, identifier_type, isbn)
        eq_(identifier, identifier2)
        eq_(False, was_new)

class TestContributor(DatabaseTest):

    def test_lookup_by_viaf(self):

        # Two contributors named Bob.
        bob1, new = Contributor.lookup(self._db, name="Bob", viaf="foo")
        bob2, new = Contributor.lookup(self._db, name="Bob", viaf="bar")

        assert bob1 != bob2

        eq_((bob1, False), Contributor.lookup(self._db, viaf="foo"))

    def test_lookup_by_lc(self):

        # Two contributors named Bob.
        bob1, new = Contributor.lookup(self._db, name="Bob", lc="foo")
        bob2, new = Contributor.lookup(self._db, name="Bob", lc="bar")

        assert bob1 != bob2

        eq_((bob1, False), Contributor.lookup(self._db, lc="foo"))

    def test_lookup_by_name(self):

        # Two contributors named Bob.
        bob1, new = Contributor.lookup(self._db, name="Bob", lc="foo")
        bob2, new = Contributor.lookup(self._db, name="Bob", lc="bar")

        # Lookup by name finds both of them.
        bobs, new = Contributor.lookup(self._db, name="Bob")
        eq_(False, new)
        eq_(["Bob", "Bob"], [x.name for x in bobs])

    def test_create_by_lookup(self):
        [bob1], new = Contributor.lookup(self._db, name="Bob")
        eq_("Bob", bob1.name)
        eq_(True, new)

        [bob2], new = Contributor.lookup(self._db, name="Bob")
        eq_(bob1, bob2)
        eq_(False, new)

class TestWorkRecord(DatabaseTest):

    def test_for_foreign_id(self):
        """Verify we can get a data source's view of a foreign id."""
        data_source = DataSource.lookup(self._db, DataSource.GUTENBERG)
        id = "549"
        type = WorkIdentifier.GUTENBERG_ID

        record, was_new = WorkRecord.for_foreign_id(
            self._db, data_source, type, id)
        eq_(data_source, record.data_source)
        identifier = record.primary_identifier
        eq_(id, identifier.identifier)
        eq_(type, identifier.type)
        eq_(True, was_new)
        eq_([identifier.id], record.equivalent_identifier_ids())

        # We can get the same work record by providing only the name
        # of the data source.
        record, was_new = WorkRecord.for_foreign_id(
            self._db, DataSource.GUTENBERG, type, id)
        eq_(data_source, record.data_source)
        eq_(identifier, record.primary_identifier)
        eq_(False, was_new)

    def test_missing_coverage_from(self):
        gutenberg = DataSource.lookup(self._db, DataSource.GUTENBERG)
        oclc = DataSource.lookup(self._db, DataSource.OCLC)
        web = DataSource.lookup(self._db, DataSource.WEB)

        # Here are two Gutenberg records.
        g1, ignore = WorkRecord.for_foreign_id(
            self._db, gutenberg, WorkIdentifier.GUTENBERG_ID, "1")

        g2, ignore = WorkRecord.for_foreign_id(
            self._db, gutenberg, WorkIdentifier.GUTENBERG_ID, "2")

        # One of them is equivalent to an OCLC record.
        o, ignore = WorkRecord.for_foreign_id(
            self._db, oclc, WorkIdentifier.OCLC_WORK, "10034")
        g1.primary_identifier.equivalent_to(oclc, o.primary_identifier)

        # Here's a web record, just sitting there.
        w, ignore = WorkRecord.for_foreign_id(
            self._db, web, WorkIdentifier.URI, "http://www.foo.com/")

        # missing_coverage_from picks up the Gutenberg record with no
        # corresponding record from OCLC. It doesn't pick up the other
        # Gutenberg record, and it doesn't pick up the web record.
        [in_gutenberg_but_not_in_oclc] = WorkRecord.missing_coverage_from(
            self._db, WorkIdentifier.GUTENBERG_ID, WorkIdentifier.OCLC_WORK, WorkIdentifier.OCLC_NUMBER)

        eq_(g2, in_gutenberg_but_not_in_oclc)

        # We can pick up the web record by doing a lookup by URI instead of Gutenberg ID.
        [in_web_but_not_in_oclc] = WorkRecord.missing_coverage_from(
            self._db, WorkIdentifier.URI, WorkIdentifier.OCLC_WORK, WorkIdentifier.OCLC_NUMBER)
        eq_(w, in_web_but_not_in_oclc)

    def test_recursive_workrecord_equivalence(self):

        gutenberg_source = DataSource.lookup(self._db, DataSource.GUTENBERG)
        open_library_source = DataSource.lookup(self._db, DataSource.OPEN_LIBRARY)
        web_source = DataSource.lookup(self._db, DataSource.WEB)

        # Here's a WorkRecord for a Project Gutenberg text.
        gutenberg, ignore = WorkRecord.for_foreign_id(
            self._db, gutenberg_source, WorkIdentifier.GUTENBERG_ID, "1")
        gutenberg.title = "Original Gutenberg text"

        # Here's a WorkRecord for an Open Library text.
        open_library, ignore = WorkRecord.for_foreign_id(
            self._db, open_library_source, WorkIdentifier.OPEN_LIBRARY_ID,
            "W1111")
        open_library.title = "Open Library record"

        # We've learned from OCLC Classify that the Gutenberg text is
        # equivalent to a certain OCLC Number. We've learned from OCLC
        # Linked Data that the Open Library text is equivalent to the
        # same OCLC Number.
        oclc_classify = DataSource.lookup(self._db, DataSource.OCLC)
        oclc_linked_data = DataSource.lookup(self._db, DataSource.OCLC_LINKED_DATA)

        oclc_number, ignore = WorkIdentifier.for_foreign_id(
            self._db, WorkIdentifier.OCLC_NUMBER, "22")
        gutenberg.primary_identifier.equivalent_to(oclc_classify, oclc_number)
        open_library.primary_identifier.equivalent_to(
            oclc_linked_data, oclc_number)
       
        # Here's a WorkRecord for a Recovering the Classics cover.
        recovering, ignore = WorkRecord.for_foreign_id(
            self._db, web_source, WorkIdentifier.URI, 
            "http://recoveringtheclassics.com/pride-and-prejudice.jpg")
        recovering.title = "Recovering the Classics cover"

        # We've manually associated that WorkRecord's URI directly
        # with the Project Gutenberg text.
        manual = DataSource.lookup(self._db, DataSource.MANUAL)
        gutenberg.primary_identifier.equivalent_to(
            manual, recovering.primary_identifier)

        # Finally, here's a completely unrelated WorkRecord, which
        # will not be showing up.
        gutenberg2, ignore = WorkRecord.for_foreign_id(
            self._db, gutenberg_source, WorkIdentifier.GUTENBERG_ID, "2")
        gutenberg2.title = "Unrelated Gutenberg record."

        # When we call equivalent_workrecords on the Project Gutenberg
        # WorkRecord, we get three WorkRecords: the Gutenberg record
        # itself, the Open Library record, and the Recovering the
        # Classics record.
        #
        # We get the Open Library record because it's associated with
        # the same OCLC Number as the Gutenberg record. We get the
        # Recovering the Classics record because it's associated
        # directly with the Gutenberg record.
        results = list(gutenberg.equivalent_work_records())
        eq_(3, len(results))
        assert gutenberg in results
        assert open_library in results
        assert recovering in results

        # Here's a Work that incorporates one of the Gutenberg records.
        work = Work()
        work.work_records.extend([gutenberg2])

        # Its set-of-all-workrecords contains only one record.
        eq_(1, work.all_workrecords().count())

        # If we add the other Gutenberg record to it, then its
        # set-of-all-workrecords is extended by that record, *plus*
        # all the WorkRecords equivalent to that record.
        work.work_records.extend([gutenberg])
        eq_(4, work.all_workrecords().count())

class TestLicensePool(DatabaseTest):

    def test_for_foreign_id(self):
        """Verify we can get a LicensePool for a data source and an 
        appropriate work identifier."""
        pool, was_new = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "541")
        eq_(True, was_new)
        eq_(DataSource.GUTENBERG, pool.data_source.name)
        eq_(WorkIdentifier.GUTENBERG_ID, pool.identifier.type)
        eq_("541", pool.identifier.identifier)
        

    def test_no_license_pool_for_data_source_that_offers_no_licenses(self):
        """OCLC doesn't offer licenses. It only provides metadata. We can get
        a WorkRecord for OCLC's view of a book, but we cannot get a
        LicensePool for OCLC's view of a book.
        """
        assert_raises_regexp(
            ValueError, 
            'Data source "OCLC Classify" does not offer licenses',
            LicensePool.for_foreign_id,
            self._db, DataSource.OCLC, "1015", 
            WorkIdentifier.OCLC_WORK)

    def test_no_license_pool_for_non_primary_identifier(self):
        """Overdrive offers licenses, but to get an Overdrive license pool for
        a book you must identify the book by Overdrive's primary
        identifier, not some other kind of identifier.
        """
        assert_raises_regexp(
            ValueError, 
            "License pools for data source 'Overdrive' are keyed to identifier type 'Overdrive ID' \(not 'ISBN', which was provided\)",
            LicensePool.for_foreign_id,
            self._db, DataSource.OVERDRIVE, WorkIdentifier.ISBN, "{1-2-3}")

    def test_with_no_work(self):
        p1, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "1")

        p2, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.OVERDRIVE, WorkIdentifier.OVERDRIVE_ID, "2")

        work, ignore = get_one_or_create(self._db, Work, title="Foo")
        p1.work = work
        
        assert p1 in work.license_pools

        eq_([p2], LicensePool.with_no_work(self._db))

class TestWork(DatabaseTest):

    def test_calculate_presentation(self):

        gutenberg_source = DataSource.lookup(self._db, DataSource.GUTENBERG)

        wr1, ignore = WorkRecord.for_foreign_id(
            self._db, gutenberg_source, WorkIdentifier.GUTENBERG_ID, "1")
        wr1.title = "Title 1"
        wr1.add_contributor("Bob", Contributor.AUTHOR_ROLE)

        wr2, ignore = WorkRecord.for_foreign_id(
            self._db, gutenberg_source, WorkIdentifier.GUTENBERG_ID, "2")
        wr2.title = "Title 2"
        wr2.add_contributor("Bob", Contributor.AUTHOR_ROLE)
        wr2.add_contributor("Alice", Contributor.AUTHOR_ROLE)

        wr3, ignore = WorkRecord.for_foreign_id(
            self._db, gutenberg_source, WorkIdentifier.GUTENBERG_ID, "3")
        wr3.title = "Title 2"
        wr3.add_contributor("Bob", Contributor.AUTHOR_ROLE)
        wr3.add_contributor("Alice", Contributor.AUTHOR_ROLE)

        work = Work()
        work.work_records.extend([wr1, wr2, wr3])

        # The title of the Work is the most common title among
        # its associated WorkRecords.
        eq_(None, work.title)
        work.calculate_presentation()
        eq_("Title 2", work.title)

        # Bob was listed as an author for all three WorkRecords,
        # making him the most popular author, so he's listed as the
        # author of the work.
        #
        # TODO: We currently can't handle multiple authors. This needs
        # to be fixed.
        eq_("Bob", work.authors)

class TestCirculationEvent(DatabaseTest):

    def _event_data(self, **kwargs):
        for k, default in (
                ("source", DataSource.OVERDRIVE),
                ("id_type", WorkIdentifier.OVERDRIVE_ID),
                ("start", datetime.datetime.utcnow()),
                ("type", CirculationEvent.LICENSE_ADD),
        ):
            kwargs.setdefault(k, default)
        if 'old_value' in kwargs and 'new_value' in kwargs:
            kwargs['delta'] = kwargs['new_value'] - kwargs['old_value']
        return kwargs

    def test_create_event_from_string(self):
        pass

    def test_create_event_from_dict(self):
        pass

    def test_new_title(self):

        # Here's a new title.
        data = self._event_data(
            source=DataSource.OVERDRIVE,
            id="{1-2-3}",
            type=CirculationEvent.LICENSE_ADD,
            old_value=0,
            new_value=2,
        )
        
        # Turn it into an event and see what happens.
        event, ignore = CirculationEvent.from_dict(self._db, data)

        # The event is associated with the correct data source.
        eq_(DataSource.OVERDRIVE, event.license_pool.data_source.name)

        # The event identifies a work by its ID plus the data source's
        # primary identifier.
        eq_(WorkIdentifier.OVERDRIVE_ID, event.license_pool.identifier.type)
        eq_("{1-2-3}", event.license_pool.identifier.identifier)

        # The number of licenses has been set to the new value.
        eq_(2, event.license_pool.licenses_owned)

    def test_update_from_event(self):

        generic_event = dict(
            source=DataSource.THREEM,
            id="a1d45",
        )

        # Here's a new title. Ten copies available.
        data = self._event_data(
            type=CirculationEvent.LICENSE_ADD,
            old_value=0,
            new_value=10,
            **generic_event
        )
        event, ignore = CirculationEvent.from_dict(self._db, data)
        pool = event.license_pool
        eq_(10, pool.licenses_available)

        # All ten copies get checked out.
        data = self._event_data(
            type=CirculationEvent.CHECKOUT,
            old_value=10,
            new_value=0,
            **generic_event
        )
        event, ignore = CirculationEvent.from_dict(self._db, data)
        eq_(0, pool.licenses_available)

        # Three patrons place holds.
        data = self._event_data(
            type=CirculationEvent.HOLD_PLACE,
            old_value=0,
            new_value=3,
            **generic_event
        )
        event, ignore = CirculationEvent.from_dict(self._db, data)
        eq_(0, pool.licenses_available)
        eq_(3, pool.patrons_in_hold_queue)

        # One patron leaves the hold queue.
        data = self._event_data(
            type=CirculationEvent.HOLD_RELEASE,
            old_value=3,
            new_value=2,
            **generic_event
        )
        event, ignore = CirculationEvent.from_dict(self._db, data)
        eq_(0, pool.licenses_available)
        eq_(2, pool.patrons_in_hold_queue)

        # One patron checks in the book.
        data = self._event_data(
            type=CirculationEvent.CHECKIN,
            old_value=0,
            new_value=1,
            **generic_event
        )
        event, ignore = CirculationEvent.from_dict(self._db, data)
        # This temporarily creates an inconsistent state.
        eq_(1, pool.licenses_available)
        eq_(2, pool.patrons_in_hold_queue)

        # But a signal is then sent to the next person in the queue
        # saying that the book is available.
        data = self._event_data(
            type=CirculationEvent.AVAILABILITY_NOTIFY,
            delta=1,
            **generic_event
        )
        event, ignore = CirculationEvent.from_dict(self._db, data)
        eq_(0, pool.licenses_available)
        eq_(1, pool.patrons_in_hold_queue)
        eq_(1, pool.licenses_reserved)

        # That person checks the book out.

        # TODO: at this point we run into a problem--without tracking
        # internal patron ID, we don't know whether a checkout is
        # caused by someone who has a reserved copy or someone who is
        # checking out from the general pool.

class TestWorkQuality(DatabaseTest):

    def test_better_known_work_gets_higher_rating(self):

        gutenberg_source = DataSource.lookup(self._db, DataSource.GUTENBERG)

        wr1_1, ignore = WorkRecord.for_foreign_id(
            self._db, gutenberg_source, WorkIdentifier.GUTENBERG_ID, "1")

        wr1_2, ignore = WorkRecord.for_foreign_id(
            self._db, gutenberg_source, WorkIdentifier.GUTENBERG_ID, "2")

        wr2_1, ignore = WorkRecord.for_foreign_id(
            self._db, gutenberg_source, WorkIdentifier.GUTENBERG_ID, "3")

        work1 = Work()
        work1.work_records.extend([wr1_1, wr1_2])

        work2 = Work()
        work2.work_records.extend([wr2_1])

        work1.calculate_quality()
        work2.calculate_quality()

        assert work1.quality > work2.quality

class TestWorkConsolidation(DatabaseTest):

    # Versions of Work and WorkRecord instrumented to bypass the
    # normal similarity comparison process.

    def setup(self):
        super(TestWorkConsolidation, self).setup()
        # Replace the complex implementations of similarity_to with 
        # much simpler versions that let us simply say which objects 
        # are to be considered similar.
        def similarity_to(self, other):
            if other in getattr(self, 'similar', []):
                return 1
            return 0
        self.old_w = Work.similarity_to
        self.old_wr = WorkRecord.similarity_to
        Work.similarity_to = similarity_to
        WorkRecord.similarity_to = similarity_to

    def teardown(self):
        Work.similarity_to = self.old_w
        WorkRecord.similarity_to = self.old_wr
        super(TestWorkConsolidation, self).teardown()

    def test_calculate_work_for_licensepool_where_primary_work_record_has_work(self):
        # This is the easy case.
        args = [self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID,
                "1"]
        # Here's a LicensePool for a book from Gutenberg.
        license, ignore = LicensePool.for_foreign_id(*args)

        # Here's a WorkRecord for the same Gutenberg book.
        work_record, ignore = WorkRecord.for_foreign_id(*args)

        # The WorkRecord has a Work associated with it.
        work = Work()
        work_record.work = work

        eq_(None, license.work)
        license.calculate_work()

        # Now, the LicensePool has the same Work associated with it.
        eq_(work, license.work)

    def test_calculate_work_for_licensepool_creates_new_work(self):

        # This work record is unique to the existing work.
        wr1, ignore = WorkRecord.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "1")
        preexisting_work = Work()
        preexisting_work.work_records = [wr1]

        # This work record is unique to the new LicensePool
        wr2, ignore = WorkRecord.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "3")
        pool, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "3")

        work, created = pool.calculate_work()
        eq_(True, created)
        assert work != preexisting_work

    def test_calculate_work_for_licensepool_uses_existing_work(self):
        pass

    def test_calculate_work_for_licensepool_merges_works_as_side_effect(self):
        pass


    def test_calculate_work_for_new_work(self):
        # TODO: This test doesn't actually test
        # anything. calculate_work() is too complicated and needs to
        # be refactored.

        # This work record is unique to the existing work.
        wr1, ignore = WorkRecord.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "1")

        # This work record is shared by the existing work and the new
        # LicensePool.
        wr2, ignore = WorkRecord.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "2")

        # These work records are unique to the new LicensePool.

        wr3, ignore = WorkRecord.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "3")

        wr4, ignore = WorkRecord.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "4")

        # Make wr4's primary identifier equivalent to wr3's and wr1's
        # primaries.
        data_source = DataSource.lookup(self._db, DataSource.OCLC_LINKED_DATA)
        for make_equivalent in wr3, wr1:
            wr4.primary_identifier.equivalent_to(
                data_source, make_equivalent.primary_identifier)
        preexisting_work = Work()
        preexisting_work.work_records.extend([wr1, wr2])

        pool, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "4")
        self._db.commit()

        pool.calculate_work()

    def test_merge_into(self):

        # Here's a work with a license pool and two work records.
        pool_1a, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "1")
        work_record_1a, ignore = WorkRecord.for_foreign_id(
            self._db, DataSource.OCLC, WorkIdentifier.OCLC_WORK, "W1")
        work_record_1b, ignore = WorkRecord.for_foreign_id(
            self._db, DataSource.OCLC, WorkIdentifier.OCLC_WORK, "W2")

        work1 = Work()
        work1.license_pools = [pool_1a]
        work1.work_records = [work_record_1a, work_record_1b]

        # Here's a work with two license pools and one work record.
        pool_2a, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "2")
        pool_2b, ignore = LicensePool.for_foreign_id(
            self._db, DataSource.GUTENBERG, WorkIdentifier.GUTENBERG_ID, "2")

        work_record_2a, ignore = WorkRecord.for_foreign_id(
            self._db, DataSource.OCLC, WorkIdentifier.OCLC_WORK, "W3")
        work_record_2a.title = "The only title in this whole test."

        work2 = Work()
        work2.license_pools = [pool_2a]
        work2.work_records = [work_record_2a]

        self._db.commit()

        # This attempt to merge the two work records will fail because
        # they don't meet the similarity threshold.
        work2.merge_into(work1, similarity_threshold=1)

        assert work2 not in self._db.deleted

        # This attempt will succeed because we lower the similarity
        # threshold.
        work2.merge_into(work1, similarity_threshold=0)

        # The merged Work has been deleted.
        assert work2 in self._db.deleted

        # The remaining Work has all three license pools.
        for p in pool_1a, pool_2a, pool_2b:
            assert p in work1.license_pools

        # It has all three work records.
        for w in work_record_1a, work_record_1b, work_record_2a:
            assert w in work1.work_records

        # Its presentation has been updated and its title now comes from
        # one of the now-deleted Work's WorkRecords.
        eq_("The only title in this whole test.", work1.title)
