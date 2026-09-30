import pytest


@pytest.fixture(autouse=True)
def _dry_run_on_by_default(monkeypatch):
    """Tests never inherit DRY_RUN from the developer's .env; a test that needs real booking sets it to 0."""
    monkeypatch.setenv("DRY_RUN", "1")
