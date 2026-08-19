"""Fixtures for the openHASP tests."""
import pytest


@pytest.fixture
def enable_openhasp_integration(enable_custom_integrations):
    """Allow loading the custom component from the repository."""
    yield
