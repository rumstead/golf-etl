from datetime import timedelta

import pytest

from golf_etl.config import Settings
from golf_etl.storage.retention import sweep_failed
from tests.fake_store import FakeStore

DAY = timedelta(days=1)


@pytest.fixture
def store():
    return FakeStore()


def test_failed_originals_expire_three_days_after_they_failed(store):
    store.add("failed/old.MOV", modified=store.now - 400 * DAY)  # shot long ago
    store.add("failed/old.MOV.error.txt", modified=store.now - 4 * DAY)
    store.add("failed/recent.MOV", modified=store.now - 400 * DAY)
    store.add("failed/recent.MOV.error.txt", modified=store.now - 2 * DAY)
    sweep_failed(store, Settings(), store.now)
    assert store.names_in("failed") == ["recent.MOV", "recent.MOV.error.txt"]
