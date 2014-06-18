import pkgutil
from nose.tools import eq_, set_trace

from integration.millenium_patron import MilleniumPatronAPI

class DummyResponse(object):
    def __init__(self, content):
        self.content = content

class DummyAPI(MilleniumPatronAPI):

    def __init__(self):
        self.queue = []
        self.root = "http://foo/"

    def enqueue(self, filename):
        data = pkgutil.get_data(
            "tests.integration",
            "files/millenium_patron/%s" % filename)
        self.queue.append(data)

    def request(self, *args, **kwargs):
        return DummyResponse(self.queue.pop())


class TestMilleniumPatronAPI(object):

    def setUp(self):
        self.api = DummyAPI()

    def test_dump_no_such_barcode(self):
        self.api.enqueue("dump.no such barcode.html")
        eq_(None, self.api.dump("bad barcode"))

    def test_dump_success(self):
        self.api.enqueue("dump.success.html")
        response = self.api.dump("good barcode")
        # TODO: what do we actually want here?

    def test_pintest_no_such_barcode(self):
        self.api.enqueue("pintest.no such barcode.html")
        eq_(True, self.api.pintest("wrong barcode", "pin"))

    def test_pintest_wrong_pin(self):
        self.api.enqueue("pintest.bad.html")
        eq_(False, self.api.pintest("barcode", "wrong pin"))

    def test_pintest_correct_pin(self):
        self.api.enqueue("pintest.good.html")
        eq_(True, self.api.pintest("barcode", "correct pin"))