import pytest


def pytest_addoption(parser):
    parser.addoption("--live", action="store_true",
                     help="run tests against strava.com (needs OPENSTRIDE_TEST_CLUB_ID and a saved session)")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--live"):
        skip = pytest.mark.skip(reason="live test: pass --live")
        for item in items:
            if "live" in item.keywords:
                item.add_marker(skip)
