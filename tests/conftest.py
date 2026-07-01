import pytest


def pytest_collection_modifyitems(config, items):
    skip_integration = pytest.mark.skip(reason="needs --run-integration")
    skip_e2e = pytest.mark.skip(reason="needs --run-e2e")
    for item in items:
        if "integration" in item.keywords and not config.getoption("--run-integration"):
            item.add_marker(skip_integration)
        if "e2e" in item.keywords and not config.getoption("--run-e2e"):
            item.add_marker(skip_e2e)


def pytest_addoption(parser):
    parser.addoption("--run-integration", action="store_true", default=False)
    parser.addoption("--run-e2e", action="store_true", default=False)
