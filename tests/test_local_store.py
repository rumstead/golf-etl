"""LocalStore on a temp folder."""

import hashlib

import pytest

from golf_etl.storage.local import LocalStore


@pytest.fixture
def root(tmp_path):
    (tmp_path / "golf").mkdir()
    return tmp_path / "golf"


@pytest.fixture
def store(root):
    return LocalStore(str(root))


def test_list_returns_files_with_sha256_and_mime(store, root):
    (root / "inbox").mkdir()
    (root / "inbox" / "Bob's swing #2.MOV").write_bytes(b"swing")
    entries = store.list("inbox", hashes=True)
    assert [e.path for e in entries] == ["inbox/Bob's swing #2.MOV"]
    e = entries[0]
    assert e.name == "Bob's swing #2.MOV"
    assert e.sha256 == hashlib.sha256(b"swing").hexdigest()
    assert e.is_video and not e.is_dir
    assert e.size == 5


def test_list_without_hashes_leaves_sha256_empty(store, root):
    (root / "inbox").mkdir()
    (root / "inbox" / "a.MOV").write_bytes(b"a")
    assert store.list("inbox")[0].sha256 is None


def test_list_of_a_missing_folder_is_empty(store):
    assert store.list("nope") == []


def test_list_has_folders_and_leaves_their_hash_empty(store, root):
    (root / "failed" / "sub").mkdir(parents=True)
    [e] = store.list("failed", hashes=True)
    assert e.is_dir and e.sha256 is None and e.size == 0


def test_recursive_list_has_nested_paths(store, root):
    (root / "x" / "s1" / "swing-01").mkdir(parents=True)
    (root / "x" / "s1" / "swing-01" / "clip.mp4").write_bytes(b"mp4")
    paths = {e.path for e in store.list("x", recursive=True)}
    assert paths == {"x/s1", "x/s1/swing-01", "x/s1/swing-01/clip.mp4"}


def test_move_renames_files_and_creates_the_target_folder(store, root):
    (root / "inbox").mkdir()
    (root / "inbox" / "a.MOV").write_bytes(b"a")
    store.move("inbox/a.MOV", "processing/a.MOV")
    assert (root / "processing" / "a.MOV").read_bytes() == b"a"
    assert not (root / "inbox" / "a.MOV").exists()


def test_move_of_a_missing_file_raises(store):
    with pytest.raises(OSError):
        store.move("inbox/missing.MOV", "processing/missing.MOV")


def test_download_and_text(store, root, tmp_path):
    (root / "inbox").mkdir()
    (root / "inbox" / "a.MOV").write_bytes(b"video")
    dest = tmp_path / "down.MOV"
    store.download("inbox/a.MOV", dest)
    assert dest.read_bytes() == b"video"
    store.write_text(".golf-etl/state.json", '{"attempts": {}}')
    assert store.read_text(".golf-etl/state.json") == '{"attempts": {}}'
    store.write_text(".golf-etl/state.json", "{}")
    assert store.read_text(".golf-etl/state.json") == "{}"
    assert sorted(p.name for p in (root / ".golf-etl").iterdir()) == ["state.json"]
    assert store.read_text(".golf-etl/missing.json") is None


def test_delete_removes_files_and_whole_folders(store, root):
    (root / "failed").mkdir()
    (root / "failed" / "bad.MOV").write_bytes(b"x")
    store.delete("failed/bad.MOV")
    assert not (root / "failed" / "bad.MOV").exists()
    (root / "x" / "s1" / "swing-01").mkdir(parents=True)
    (root / "x" / "s1" / "swing-01" / "a.jpg").write_bytes(b"x")
    store.delete("x/s1")
    assert not (root / "x" / "s1").exists()


def test_mkdir_is_idempotent(store, root):
    store.mkdir("inbox")
    store.mkdir("inbox")
    assert (root / "inbox").is_dir()
