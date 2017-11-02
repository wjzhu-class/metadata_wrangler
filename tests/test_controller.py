import os
import base64
import feedparser
import json
import re
import urllib
from Crypto.Cipher import PKCS1_OAEP
from Crypto.PublicKey import RSA
from StringIO import StringIO
from datetime import datetime, timedelta
from functools import wraps
from lxml import etree
from nose.tools import set_trace, eq_

from . import (
    DatabaseTest,
    sample_data
)
from core.config import Configuration
from core.model import (
    Collection,
    ConfigurationSetting,
    CoverageRecord,
    DataSource,
    Edition,
    ExternalIntegration,
    Hyperlink,
    Identifier,
    IntegrationClient,
    get_one,
)
from core.opds_import import OPDSXMLParser
from core.testing import (
    DummyHTTPClient,
    MockRequestsResponse,
)
from core.util.problem_detail import ProblemDetail
from core.util.opds_writer import OPDSMessage

from controller import (
    CatalogController,
    IndexController,
    URNLookupController,
    HTTP_OK,
    HTTP_CREATED,
    HTTP_ACCEPTED,
    HTTP_UNAUTHORIZED,
    HTTP_NOT_FOUND,
    HTTP_INTERNAL_SERVER_ERROR,
    authenticated_client_from_request,
    collection_from_details,
)
from problem_details import *

from coverage import IdentifierResolutionRegistrar

class ControllerTest(DatabaseTest):

    def setup(self):
        super(ControllerTest, self).setup()

        from app import app
        self.app = app

        self.client = self._integration_client()
        valid_auth = 'Bearer ' + base64.b64encode(self.client.shared_secret)
        self.valid_auth = dict(Authorization=valid_auth)

    def sample_data(self, filename):
        return sample_data(filename, 'controller')


class TestIntegrationClientAuthentication(ControllerTest):

    def test_authenticated_client_required(self):
        # Returns catalog if authentication is valid.
        with self.app.test_request_context('/', headers=self.valid_auth):
            result = authenticated_client_from_request(self._db)
            eq_(result, self.client)

        # Returns error if authentication is invalid.
        invalid_auth = 'Bearer ' + base64.b64encode('wrong_secret')
        with self.app.test_request_context('/',
                headers=dict(Authorization=invalid_auth)):
            result = authenticated_client_from_request(self._db)
            eq_(True, isinstance(result, ProblemDetail))
            eq_(HTTP_UNAUTHORIZED, result.status_code)

        # Returns errors without authentication.
        with self.app.test_request_context('/'):
            result = authenticated_client_from_request(self._db)
            eq_(True, isinstance(result, ProblemDetail))

    def test_authenticated_client_optional(self):
        # Returns catalog of authentication is valid.
        with self.app.test_request_context('/', headers=self.valid_auth):
            result = authenticated_client_from_request(self._db, required=False)
            eq_(result, self.client)

        # Returns error if attempted authentication is invalid.
        invalid_auth = 'Basic ' + base64.b64encode('abc:defg')
        with self.app.test_request_context('/',
                headers=dict(Authorization=invalid_auth)):
            result = authenticated_client_from_request(self._db, required=False)
            eq_(True, isinstance(result, ProblemDetail))
            eq_(HTTP_UNAUTHORIZED, result.status_code)

        # Returns none if no authentication.
        with self.app.test_request_context('/'):
            result = authenticated_client_from_request(self._db, required=False)
            eq_(None, result)


class TestCollectionHandling(ControllerTest):

    def test_collection_from_details(self):
        mirrored_collection = self._collection(external_account_id=self._url)
        details = mirrored_collection.metadata_identifier

        collection = None
        with self.app.test_request_context('/'):
            # Without a information, nothing is returned.
            result = collection_from_details(self._db, None, None)
            eq_(None, result)

            result = collection_from_details(self._db, self.client, None)
            eq_(None, result)

            result = collection_from_details(self._db, None, details)
            eq_(None, result)

            # It creates a collection if it doesn't exist.
            result = collection_from_details(self._db, self.client, details)
            assert isinstance(result, Collection)
            collection = result

        # The DataSource can also be set via arguments.
        eq_(None, collection.data_source)
        source = 'data_source=%s' % urllib.quote(DataSource.OA_CONTENT_SERVER)
        with self.app.test_request_context('/?%s' % source):
            result = collection_from_details(self._db, self.client, details)

            # The previously-created collection is returned.
            eq_(collection, result)

            # It has a DataSource.
            eq_(DataSource.OA_CONTENT_SERVER, collection.data_source.name)


class TestIndexController(ControllerTest):

    def test_opds_catalog(self):
        controller = IndexController(self._db)
        with self.app.test_request_context('/'):
            response = controller.opds_catalog()

        eq_(200, response.status_code)
        catalog = json.loads(response.data)

        app_url = ConfigurationSetting.sitewide(self._db, Configuration.BASE_URL_KEY).value
        eq_(app_url, catalog.get('id'))
        urls = [l.get('href') for l in catalog.get('links')]

        # Use flask endpoint syntax for path variables
        urls = [re.sub('\{', '<', url) for url in urls]
        urls = [re.sub('\}', '>', url) for url in urls]
        # Remove arguments from templated urls
        urls = [re.sub('<\?[\w,_]*\*?>', '', url) for url in urls]

        # Compare the catalogued urls with the app endpoints.
        endpoints = [r.rule for r in self.app.url_map.iter_rules()]
        for url in urls:
            assert url in endpoints


class TestCatalogController(ControllerTest):

    XML_PARSE = OPDSXMLParser()._xpath

    def setup(self):
        super(TestCatalogController, self).setup()
        self.controller = CatalogController(self._db)
        self.http = DummyHTTPClient()

        # The collection as it exists on the circulation manager.
        remote_collection = self._collection(username='test_coll', external_account_id=self._url)
        # The collection as it is recorded / catalogued here.
        self.collection = self._collection(
            name=remote_collection.metadata_identifier,
            protocol=remote_collection.protocol
        )

        self.work1 = self._work(with_license_pool=True, with_open_access_download=True)
        self.work2 = self._work(with_license_pool=True, with_open_access_download=True)

    def xml_value(self, message, tag):
        return self.XML_PARSE(message, tag)[0].text

    def test_updates_feed(self):
        identifier = self.work1.license_pools[0].identifier
        self.collection.catalog_identifier(identifier)

        with self.app.test_request_context('/', headers=self.valid_auth):
            response = self.controller.updates_feed(self.collection.name)
            # The catalog's updates feed is returned.
            eq_(HTTP_OK, response.status_code)
            feed = feedparser.parse(response.get_data())
            eq_(feed.feed.title,
                u"%s Collection Updates for %s" % (self.collection.protocol, self.client.url))

            # The feed has the catalog's catalog.
            eq_(1, len(feed['entries']))
            [entry] = feed['entries']
            eq_(self.work1.title, entry['title'])
            eq_(identifier.urn, entry['id'])

        # A time can be passed.
        time = datetime.utcnow()
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ")
        for record in self.work1.coverage_records:
            # Set back the clock on all of work1's time records
            record.timestamp = time - timedelta(days=1)
        with self.app.test_request_context('/?last_update_time=%s' % timestamp,
            headers=self.valid_auth):
            response = self.controller.updates_feed(self.collection.name)
            eq_(HTTP_OK, response.status_code)
            feed = feedparser.parse(response.get_data())
            eq_(feed.feed.title,
                u"%s Collection Updates for %s" % (self.collection.protocol, self.client.url))

            # The timestamp is included in the url.
            linkified_timestamp = time.strftime("%Y-%m-%d+%H:%M:%S").replace(":", "%3A")
            assert feed['feed']['id'].endswith(linkified_timestamp)
            # And only works updated since the timestamp are returned.
            eq_(0, len(feed['entries']))

        # Works updated since the timestamp are returned
        self.work1.coverage_records[0].timestamp = datetime.utcnow()
        with self.app.test_request_context('/?last_update_time=%s' % timestamp,
            headers=self.valid_auth):
            response = self.controller.updates_feed(self.collection.name)
            feed = feedparser.parse(response.get_data())
            eq_(1, len(feed['entries']))
            [entry] = feed['entries']
            eq_(self.work1.title, entry['title'])
            eq_(identifier.urn, entry['id'])

    def test_updates_feed_is_paginated(self):
        for work in [self.work1, self.work2]:
            self.collection.catalog_identifier(work.license_pools[0].identifier)
        with self.app.test_request_context('/?size=1',
            headers=self.valid_auth):
            response = self.controller.updates_feed(self.collection.name)
            links = feedparser.parse(response.get_data())['feed']['links']
            assert any([link['rel'] == 'next' for link in links])
            assert not any([link['rel'] == 'previous' for link in links])
            assert not any([link['rel'] == 'first' for l in links])

        with self.app.test_request_context('/?size=1&after=1',
            headers=self.valid_auth):
            response = self.controller.updates_feed(self.collection.name)
            links = feedparser.parse(response.get_data())['feed']['links']
            assert any([link['rel'] == 'previous' for link in links])
            assert any([link['rel'] == 'first' for link in links])
            assert not any([link['rel'] == 'next'for link in links])

    def test_add_items(self):
        invalid_urn = "FAKE AS I WANNA BE"
        catalogued_id = self._identifier()
        uncatalogued_id = self._identifier()
        self.collection.catalog_identifier(catalogued_id)

        parser = OPDSXMLParser()
        message_path = '/atom:feed/simplified:message'

        with self.app.test_request_context(
                '/?urn=%s&urn=%s&urn=%s' % (
                catalogued_id.urn, uncatalogued_id.urn, invalid_urn),
                method='POST', headers=self.valid_auth):

            response = self.controller.add_items(self.collection.name)

        # None of the identifiers raise or return an error.
        eq_(HTTP_OK, response.status_code)

        # It sends three messages.
        root = etree.parse(StringIO(response.data))
        messages = self.XML_PARSE(root, message_path)

        # The uncatalogued identifier is now in the catalog.
        assert uncatalogued_id in self.collection.catalog
        # It has an accurate response message.
        [uncatalogued] = [m for m in messages
                          if self.xml_value(m, 'atom:id')==uncatalogued_id.urn]
        eq_(uncatalogued_id.urn, self.xml_value(uncatalogued, 'atom:id'))
        eq_('201', self.xml_value(uncatalogued, 'simplified:status_code'))
        eq_('Successfully added', self.xml_value(uncatalogued, 'schema:description'))

        # The catalogued identifier is still in the catalog.
        assert catalogued_id in self.collection.catalog
        # And even though it responds 'OK', the message tells you it
        # was already there.
        [catalogued] = [m for m in messages
                        if self.xml_value(m, 'atom:id')==catalogued_id.urn]
        eq_('200', self.xml_value(catalogued, 'simplified:status_code'))
        eq_('Already in catalog', self.xml_value(catalogued, 'schema:description'))

        # Invalid identifier return 400 errors.
        [invalid] = [m for m in messages
                     if self.xml_value(m, 'atom:id')==invalid_urn]
        eq_('400', self.xml_value(invalid, 'simplified:status_code'))
        eq_('Could not parse identifier.', self.xml_value(invalid, 'schema:description'))

    def test_add_with_metadata(self):
        # Pretend this content server OPDS came from a circulation manager.
        base_path = os.path.split(__file__)[0]
        resource_path = os.path.join(base_path, "files", "opds")
        path = os.path.join(resource_path, "content_server_lookup.opds")
        opds = open(path).read()

        # And here's some OPDS with an invalid identifier.
        invalid_opds = "<feed><entry><id>invalid</id></entry></feed>"

        parser = OPDSXMLParser()
        message_path = '/atom:feed/simplified:message'

        with self.app.test_request_context(headers=self.valid_auth, data=opds):
            response = self.controller.add_with_metadata(self.collection.name)

        eq_(HTTP_OK, response.status_code)

        # It sends one message.
        root = etree.parse(StringIO(response.data))
        [catalogued] = self.XML_PARSE(root, message_path)

        # The identifier in the OPDS feed is now in the catalog.
        identifier = self._identifier(foreign_id='20201')
        assert identifier in self.collection.catalog

        # It has an accurate response message.
        eq_(identifier.urn, self.xml_value(catalogued, 'atom:id'))
        eq_('201', self.xml_value(catalogued, 'simplified:status_code'))
        eq_('Successfully added', self.xml_value(catalogued, 'schema:description'))

        # The identifier has links for the cover images from the feed.
        eq_(set(["http://s3.amazonaws.com/book-covers.nypl.org/Gutenberg%20Illustrated/20201/cover_20201_0.png",
                 "http://s3.amazonaws.com/book-covers.nypl.org/Gutenberg%20Illustrated/20201/cover_20201_0.png"]),
            set([link.resource.url for link in identifier.links]))
        eq_(set([Hyperlink.IMAGE, Hyperlink.THUMBNAIL_IMAGE]),
            set([link.rel for link in identifier.links]))

        # The identifier has a LicensePool.
        eq_(1, len(identifier.licensed_through))
        eq_(self.collection, identifier.licensed_through[0].collection)

        # The identifier also has an Edition with title, author, and language.
        edition = get_one(self._db, Edition, primary_identifier=identifier)
        eq_("Mary Gray", edition.title)
        [author] = edition.contributors
        eq_(Edition.UNKNOWN_AUTHOR, author.sort_name)
        eq_("eng", edition.language)

        # Finally, the identifier has a transient failure CoverageRecord so it will
        # be processed by the identifier resolution script.
        data_source = DataSource.lookup(self._db, DataSource.INTERNAL_PROCESSING)
        record = CoverageRecord.lookup(identifier, data_source,
                                        CoverageRecord.RESOLVE_IDENTIFIER_OPERATION)
        eq_(CoverageRecord.TRANSIENT_FAILURE, record.status)
        eq_(self.collection, record.collection)

        record.status = CoverageRecord.SUCCESS

        # If we make the same request again, the identifier stays in the catalog.
        with self.app.test_request_context(headers=self.valid_auth, data=opds):
            response = self.controller.add_with_metadata(self.collection.name)

        eq_(HTTP_OK, response.status_code)

        # It sends one message.
        root = etree.parse(StringIO(response.data))
        [catalogued] = self.XML_PARSE(root, message_path)

        # The identifier in the OPDS feed is still in the catalog.
        assert identifier in self.collection.catalog

        # And even though it responds 'OK', the message tells you it
        # was already there.
        eq_(identifier.urn, self.xml_value(catalogued, 'atom:id'))
        eq_('200', self.xml_value(catalogued, 'simplified:status_code'))
        eq_('Already in catalog', self.xml_value(catalogued, 'schema:description'))

        # The coverage record has been set back to transient failure since
        # there's new information to process.
        eq_(CoverageRecord.TRANSIENT_FAILURE, record.status)

        # The invalid identifier returns a 400 error message.
        with self.app.test_request_context(headers=self.valid_auth, data=invalid_opds):
            response = self.controller.add_with_metadata(self.collection.name)
        eq_(HTTP_OK, response.status_code)

        # It sends one message.
        root = etree.parse(StringIO(response.data))
        [invalid] = self.XML_PARSE(root, message_path)

        eq_("invalid", self.xml_value(invalid, 'atom:id'))
        eq_('400', self.xml_value(invalid, 'simplified:status_code'))
        eq_('Could not parse identifier.', self.xml_value(invalid, 'schema:description'))

    def test_remove_items(self):
        invalid_urn = "FAKE AS I WANNA BE"
        catalogued_id = self._identifier()
        uncatalogued_id = self._identifier()
        self.collection.catalog_identifier(catalogued_id)

        message_path = '/atom:feed/simplified:message'
        with self.app.test_request_context(
                '/?urn=%s&urn=%s' % (catalogued_id.urn, uncatalogued_id.urn),
                method='POST', headers=self.valid_auth
        ):
            # The uncatalogued identifier doesn't raise or return an error.
            response = self.controller.remove_items(self.collection.name)
            eq_(HTTP_OK, response.status_code)

        # It sends two <simplified:message> tags.
        root = etree.parse(StringIO(response.data))
        messages = self.XML_PARSE(root, message_path)

        # The catalogued Identifier has been removed.
        assert catalogued_id not in self.collection.catalog
        [catalogued] = [m for m in messages
                        if self.xml_value(m, 'atom:id')==catalogued_id.urn]
        eq_(str(HTTP_OK), self.xml_value(catalogued, 'simplified:status_code'))
        eq_("Successfully removed", self.xml_value(catalogued, 'schema:description'))

        assert uncatalogued_id not in self.collection.catalog
        [uncatalogued] = [m for m in messages
                          if self.xml_value(m, 'atom:id')==uncatalogued_id.urn]
        eq_(str(HTTP_NOT_FOUND), self.xml_value(uncatalogued, 'simplified:status_code'))
        eq_("Not in catalog", self.xml_value(uncatalogued, 'schema:description'))

        # It sends no <entry> tags.
        eq_([], self.XML_PARSE(root, "//atom:entry"))

        # The catalogued identifier isn't in the catalog.
        assert catalogued_id not in self.collection.catalog
        # But it's still in the database.
        eq_(catalogued_id, self._db.query(Identifier).filter_by(
            id=catalogued_id.id).one())

        # Try again, this time including an invalid URN.
        self.collection.catalog_identifier(catalogued_id)
        with self.app.test_request_context(
                '/?urn=%s&urn=%s' % (invalid_urn, catalogued_id.urn),
                method='POST', headers=self.valid_auth
        ):
            response = self.controller.remove_items(self.collection.name)
            eq_(HTTP_OK, int(response.status_code))

        # Once again we get two <simplified:message> tags.
        root = etree.parse(StringIO(response.data))
        messages = self.XML_PARSE(root, message_path)

        [invalid] = [m for m in messages
                     if self.xml_value(m, 'atom:id')==invalid_urn]
        eq_("400", self.xml_value(invalid, 'simplified:status_code'))
        eq_("Could not parse identifier.", self.xml_value(invalid, 'schema:description'))

        [catalogued] == [m for m in messages
                         if self.xml_value(m, 'atom:id')==catalogued_id.urn]
        eq_("200", self.xml_value(catalogued, 'simplified:status_code'))
        eq_("Successfully removed", self.xml_value(catalogued, 'schema:description'))

        # We have no <entry> tags.
        eq_([], self.XML_PARSE(root, "//atom:entry"))

        # The catalogued identifier is still removed.
        assert catalogued_id not in self.collection.catalog

    def create_register_request_args(self, url):
        return dict(
            method='POST',
            data=dict(url=url),
            headers={ 'Content-Type' : 'application/x-www-form-urlencoded' }
        )

    def test_register_fails_without_url(self):
        # If not URL is given, a ProblemDetail is returned.
        request_args = self.create_register_request_args('')
        request_args['data'] = ''
        with self.app.test_request_context('/', method='POST'):
            response = self.controller.register()
        eq_(NO_AUTH_URL, response)

    def test_register_fails_if_error_is_raised_fetching_document(self):
        def error_get(*args, **kwargs):
            raise RuntimeError('An OPDS Error')

        url = "https://test.org/okay/"
        request_args = self.create_register_request_args(url)
        with self.app.test_request_context('/', **request_args):
            response = self.controller.register(do_get=error_get)

        eq_(REMOTE_INTEGRATION_ERROR, response)

    def test_register_fails_when_public_key_document_is_invalid(self):
        document_url = 'https://test.org/'
        mock_public_key_doc = json.loads(self.sample_data('public_key_document.json'))

        def assert_invalid_key_document(response, message=None):
            eq_(True, isinstance(response, ProblemDetail))
            eq_(400, response.status_code)
            eq_('Invalid integration document', str(response.title))
            assert response.uri.endswith('/invalid-integration-document')
            if message:
                assert message in response.detail

        def mock_response(content_json, status_code=200):
            content = json.dumps(content_json)
            headers = { 'Content-Type' : 'application/opds+json' }
            return MockRequestsResponse(
                status_code, headers=headers, content=content
            )

        # A ProblemDetail is returned when there is no public key document.
        self.http.responses.append(MockRequestsResponse(200, content=''))
        request_args = self.create_register_request_args(document_url)
        with self.app.test_request_context('/', **request_args):
            response = self.controller.register(do_get=self.http.do_get)
        assert_invalid_key_document(response)

        # A ProblemDetail is returned when the public key document doesn't
        # have an id.
        no_id_doc = mock_public_key_doc.copy()
        del no_id_doc['id']
        self.http.responses.append(mock_response(no_id_doc))

        request_args = self.create_register_request_args(document_url)
        with self.app.test_request_context('/', **request_args):
            response = self.controller.register(do_get=self.http.do_get)
        assert_invalid_key_document(response, 'is missing an id')

        # A ProblemDetail is returned when the public key document id
        # doesn't match the submitted OPDS url.
        self.http.responses.append(mock_response(mock_public_key_doc))
        url = 'https://fake.opds/'

        request_args = self.create_register_request_args(url)
        with self.app.test_request_context('/', **request_args):
            response = self.controller.register(do_get=self.http.do_get)
        assert_invalid_key_document(response, "doesn't match submitted url")

        # A ProblemDetail is returned when the public key document doesn't
        # have an RSA public key.
        no_key_json = mock_public_key_doc.copy()
        del no_key_json['public_key']
        self.http.responses.append(mock_response(no_key_json))

        request_args = self.create_register_request_args(document_url)
        with self.app.test_request_context('/', **request_args):
            response = self.controller.register(do_get=self.http.do_get)
        assert_invalid_key_document(response, "missing an RSA public_key")

        # There's a key, but the type isn't RSA.
        no_key_json['public_key'] = dict(type='safe', value='value')
        self.http.responses.append(mock_response(no_key_json))

        request_args = self.create_register_request_args(document_url)
        with self.app.test_request_context('/', **request_args):
            response = self.controller.register(do_get=self.http.do_get)
        assert_invalid_key_document(response, "missing an RSA public_key")

        # There's an RSA public_key property, but there's no value there.
        no_key_json['public_key']['type'] = 'RSA'
        del no_key_json['public_key']['value']
        self.http.responses.append(mock_response(no_key_json))

        request_args = self.create_register_request_args(document_url)
        with self.app.test_request_context('/', **request_args):
            response = self.controller.register(do_get=self.http.do_get)
        assert_invalid_key_document(response, "missing an RSA public_key")

    def test_register_succeeds_with_valid_public_key_document(self):
        # Create an encryptor so we can compare secrets later. :3
        key = RSA.generate(1024)
        encryptor = PKCS1_OAEP.new(key)

        # Put the new key in the mock catalog.
        mock_auth_json = json.loads(self.sample_data('public_key_document.json'))
        mock_auth_json['public_key']['value'] = key.exportKey()
        mock_public_key_doc = json.dumps(mock_auth_json)
        mock_doc_response = MockRequestsResponse(
            200, content=mock_public_key_doc,
            headers={ 'Content-Type' : 'application/opds+json' }
        )
        self.http.responses.append(mock_doc_response)

        url = 'https://test.org/'
        request_args = self.create_register_request_args(url)
        with self.app.test_request_context('/', **request_args):
            response = self.controller.register(do_get=self.http.do_get)

        # An IntegrationClient has been created for this website.
        eq_(201, response.status_code)
        client_qu = self._db.query(IntegrationClient).filter(
            IntegrationClient.url == 'test.org'
        )
        client = client_qu.one()

        # The appropriate login details are in the response.
        catalog = json.loads(response.data)
        eq_(url, catalog.get('id'))
        shared_secret = catalog.get('metadata').get('shared_secret')
        shared_secret = encryptor.decrypt(base64.b64decode(shared_secret))
        eq_(client.shared_secret, shared_secret)

        # If the client already exists, the shared_secret is updated.
        client.shared_secret = 'token'
        bearer_token = 'Bearer '+base64.b64encode('token')
        request_args['headers']['Authorization'] = bearer_token

        self.http.responses.append(mock_doc_response)
        with self.app.test_request_context('/', **request_args):
            response = self.controller.register(do_get=self.http.do_get)

        eq_(200, response.status_code)
        catalog = json.loads(response.data)
        # There's still only one IntegrationClient with this URL.
        client = client_qu.one()
        # It has a new shared_secret.
        assert client.shared_secret != 'token'
        shared_secret = catalog.get('metadata').get('shared_secret')
        shared_secret = encryptor.decrypt(base64.b64decode(shared_secret))
        eq_(client.shared_secret, shared_secret)


class TestURNLookupController(ControllerTest):

    def setup(self):
        super(TestURNLookupController, self).setup()
        self.controller = URNLookupController(self._db)
        self.source = DataSource.lookup(self._db, DataSource.INTERNAL_PROCESSING)

    def basic_request_context(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            from app import app
            with app.test_request_context('/'):
                return f(*args, **kwargs)
        return decorated

    def authenticated_request_context(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            from app import app

            secret = args[0].client.shared_secret.encode('utf8')
            valid_auth = 'Bearer '+ base64.urlsafe_b64encode(secret)
            headers = { 'Authorization' : valid_auth }
            with app.test_request_context('/', headers=headers):
                return f(*args, **kwargs)
        return decorated

    def assert_one_message(self, urn, code, message):
        """Assert that the given message is the only thing
        in the feed.
        """
        [obj] = self.controller.precomposed_entries
        expect = OPDSMessage(urn, code, message)
        assert isinstance(obj, OPDSMessage)
        eq_(urn, obj.urn)
        eq_(code, obj.status_code)
        eq_(message, obj.message)
        eq_([], self.controller.works)

    @authenticated_request_context
    def test_process_urn_initial_registration(self):
        urn = Identifier.URN_SCHEME_PREFIX + "Overdrive%20ID/nosuchidentifier"
        remote_collection = self._collection(external_account_id='banana')
        name = remote_collection.metadata_identifier

        self.controller.process_urns([urn], collection_details=name)
        self.assert_one_message(
            urn, 201, URNLookupController.IDENTIFIER_REGISTERED
        )

        # The Identifier has been added to the collection to await registration
        collection = self._db.query(Collection).filter(Collection.name==name).one()
        identifier = Identifier.parse_urn(self._db, urn)[0]
        assert identifier in collection.catalog

    @basic_request_context
    def test_process_identifier_pending_resolve_attempt(self):
        # Simulate calling process_identifier after the identifier has been
        # registered and make sure the second call results in an
        # "I'm working on it, hold your horses" message.
        identifier = self._identifier(Identifier.GUTENBERG_ID)

        source = DataSource.lookup(self._db, DataSource.INTERNAL_PROCESSING)
        operation = IdentifierResolutionRegistrar.OPERATION
        self._coverage_record(identifier, source, operation=operation)

        self.controller.process_identifier(identifier, identifier.urn)
        self.assert_one_message(
            identifier.urn, HTTP_ACCEPTED,
            URNLookupController.WORKING_TO_RESOLVE_IDENTIFIER
        )

    @basic_request_context
    def test_process_identifier_exception_during_resolve_attempt(self):
        identifier = self._identifier(Identifier.GUTENBERG_ID)
        record, is_new = CoverageRecord.add_for(
            identifier, self.source, CoverageRecord.RESOLVE_IDENTIFIER_OPERATION,
            status=CoverageRecord.TRANSIENT_FAILURE
        )
        record.exception = "foo"

        # A transient failure results in an "accepted" 201 status code.
        self.controller.process_identifier(identifier, identifier.urn)
        self.assert_one_message(
            identifier.urn, HTTP_ACCEPTED,
            self.controller.WORKING_TO_RESOLVE_IDENTIFIER
        )

        # A persistent failure results in a "server error" 500 status code.
        self.controller.precomposed_entries = []
        record.status = CoverageRecord.PERSISTENT_FAILURE
        self.controller.process_identifier(identifier, identifier.urn)
        self.assert_one_message(
            identifier.urn, HTTP_INTERNAL_SERVER_ERROR, "foo"
        )

    @basic_request_context
    def test_process_identifier_no_presentation_ready_work(self):
        identifier = self._identifier(Identifier.GUTENBERG_ID)

        # There's a record of success, but no presentation-ready work.
        record, is_new = CoverageRecord.add_for(
            identifier, self.source, CoverageRecord.RESOLVE_IDENTIFIER_OPERATION,
            status=CoverageRecord.SUCCESS
        )

        self.controller.process_identifier(identifier, identifier.urn)
        self.assert_one_message(
            identifier.urn, HTTP_INTERNAL_SERVER_ERROR,
            self.controller.SUCCESS_DID_NOT_RESULT_IN_PRESENTATION_READY_WORK
        )

    @basic_request_context
    def test_process_identifier_unresolvable_type(self):
        # We can't resolve a 3M identifier because we don't have the
        # appropriate access to the bibliographic API.
        identifier = self._identifier(Identifier.THREEM_ID)
        self.controller.process_identifier(identifier, identifier.urn)
        self.assert_one_message(
            identifier.urn, HTTP_NOT_FOUND, self.controller.UNRESOLVABLE_IDENTIFIER
        )

    @basic_request_context
    def test_presentation_ready_work_overrides_unresolveable_type(self):
        # If there is a presentation-ready Work associated
        # with the identifier, turns out we can resolve it even if the
        # type would otherwise not be resolvable.
        edition, pool = self._edition(
            identifier_type=Identifier.THREEM_ID, with_license_pool=True
        )
        pool.open_access = False
        work, is_new = pool.calculate_work()
        work.presentation_ready = True
        identifier = edition.primary_identifier
        self.controller.process_identifier(identifier, identifier.urn)
        eq_([(identifier, work)], self.controller.works)

    @basic_request_context
    def test_process_identifier_isbn(self):
        # Create a new ISBN identifier.
        # Ask online providers for metadata to turn into an opds feed about this identifier.
        # Make sure a coverage record was created, and a 201 status obtained from provider.
        # Ask online provider again, and make sure we're now getting a 202 "working on it" status.
        # Ask again, this time getting a result.  Make sure know that got a result.

        isbn, ignore = Identifier.for_foreign_id(
            self._db, Identifier.ISBN, self._isbn
        )

        # The first time we look up an ISBN
        self.controller.process_identifier(isbn, isbn.urn)
        self.assert_one_message(
            isbn.urn, HTTP_CREATED, self.controller.IDENTIFIER_REGISTERED
        )

        # So long as the necessary coverage is not provided,
        # future lookups will not provide useful information
        source = DataSource.lookup(self._db, DataSource.OCLC)
        self._coverage_record(isbn, source)

        self.controller.precomposed_entries = []
        self.controller.process_identifier(isbn, isbn.urn)
        self.assert_one_message(
            isbn.urn, HTTP_ACCEPTED, self.controller.WORKING_TO_RESOLVE_IDENTIFIER
        )

        # Let's provide the coverage.
        metadata_sources = DataSource.metadata_sources_for(
            self._db, isbn
        )
        for source in metadata_sources:
            CoverageRecord.add_for(isbn, source)

        # Process the ISBN again, and we get an <entry> tag with the
        # information.
        self.controller.precomposed_entries = []
        self.controller.process_identifier(isbn, isbn.urn)
        expect = isbn.opds_entry()
        [actual] = self.controller.precomposed_entries
        eq_(etree.tostring(expect), etree.tostring(actual))
