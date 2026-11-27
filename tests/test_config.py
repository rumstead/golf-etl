import pytest

from golf_etl.config import Settings


def test_defaults_without_env():
    s = Settings.from_env({})
    assert s.onset_min_gap_s == 8.0
    assert s.failed_ttl_days == 3


def test_env_overrides_are_cast_to_the_field_type():
    s = Settings.from_env(
        {
            "GOLF_ONSET_MIN_GAP_S": "6.5",
            "GOLF_CONFIRM_WINDOW_MS": "250",
            "GOLF_INBOX_ROOT": "/tmp/golf",
        }
    )
    assert s.onset_min_gap_s == 6.5
    assert s.confirm_window_ms == 250
    assert s.inbox_root == "/tmp/golf"


def test_unrelated_env_is_ignored():
    assert Settings.from_env({"GOLF_UNKNOWN": "x", "HOME": "/root"}) == Settings()


def test_bad_value_names_the_variable():
    with pytest.raises(ValueError, match="GOLF_FAILED_TTL_DAYS"):
        Settings.from_env({"GOLF_FAILED_TTL_DAYS": "two weeks"})


def test_storage_defaults_to_github():
    s = Settings()
    assert s.inbox_root == "/data"
    assert s.sessions_repo == "git@github.com:rumstead/golf-sessions.git"


def test_the_removed_storage_setting_is_ignored():
    assert Settings.from_env({"GOLF_STORAGE": "github"}) == Settings()
