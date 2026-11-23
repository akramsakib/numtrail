import pytest

from numtrail import discover, registry


@pytest.fixture(autouse=True)
def _clean_registry():
    """Discover the shipped modules once; drop anything a test injected."""
    discover()
    baseline = set(registry.MODULES)
    yield
    for name in set(registry.MODULES) - baseline:
        registry.MODULES.pop(name, None)
