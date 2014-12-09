import sys
import os
from nose.tools import set_trace

# Add the parent directory to the path so that import statements will work
# the same in tests as in code.
this_dir = os.path.abspath(os.path.dirname(__file__))
parent = os.path.split(this_dir)[0]
sys.path.insert(0, parent)

from testing import (
    DatabaseTest,
    _setup,
    _teardown,
)

class CoreDBInfo(object):
    connection = None
    engine = None
    transaction = None

DatabaseTest.DBInfo = CoreDBInfo

def setup():
    set_trace()
    _setup(CoreDBInfo)

def teardown():
    _teardown(DBInfo)
