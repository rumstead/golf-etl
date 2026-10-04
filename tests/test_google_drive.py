"""GoogleDrive against a recording stand-in for the googleapiclient service."""

from datetime import UTC, datetime

from golf_etl.drive.google import GoogleDrive


class Call:
    def __init__(self, recorder, method, kwargs):
        self.recorder, self.method, self.kwargs = recorder, method, kwargs

    def execute(self, num_retries=0):
        self.recorder.calls.append((self.method, self.kwargs))
        responses = self.recorder.responses.get(self.method, [])
        return responses.pop(0) if responses else {"id": "new"}


class Files:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.responses: dict[str, list[dict]] = {}

    def __getattr__(self, method):
        return lambda **kwargs: Call(self, method, kwargs)


class Service:
    def __init__(self):
        self._files = Files()

    def files(self):
        return self._files


def api_file(i, name="IMG_1.MOV", **extra):
    return {
        "id": f"id{i}",
        "name": name,
        "mimeType": "video/quicktime",
        "parents": ["inbox"],
        "createdTime": "2026-10-04T12:00:00.000Z",
        "size": "42",
        **extra,
    }


def test_list_children_follows_pages_and_maps_fields():
    service = Service()
    service.files().responses["list"] = [
        {"files": [api_file(1, sha256Checksum="abc")], "nextPageToken": "p2"},
        {"files": [api_file(2, appProperties={"attempts": "1"})]},
    ]
    files = GoogleDrive(service).list_children("inbox")
    assert [f.id for f in files] == ["id1", "id2"]
    assert files[0].sha256 == "abc" and files[0].size == 42
    assert files[0].created == datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
    assert files[1].app_properties == {"attempts": "1"}
    calls = service.files().calls
    assert calls[0][1]["q"] == "'inbox' in parents and trashed = false"
    assert calls[1][1]["pageToken"] == "p2"


def test_find_by_property_quotes_values():
    service = Service()
    GoogleDrive(service).find_by_property("sessions", "sourceName", "it's.MOV")
    q = service.files().calls[0][1]["q"]
    assert q == (
        "'sessions' in parents and appProperties has "
        "{ key='sourceName' and value='it\\'s.MOV' } and trashed = false"
    )


def test_move_replaces_all_current_parents():
    service = Service()
    service.files().responses["get"] = [{"parents": ["inbox"]}]
    GoogleDrive(service).move("id1", "processing")
    method, kwargs = service.files().calls[-1]
    assert method == "update"
    assert kwargs["addParents"] == "processing" and kwargs["removeParents"] == "inbox"


def test_delete_is_permanent_not_trash():
    service = Service()
    GoogleDrive(service).delete("id1")
    assert service.files().calls == [("delete", {"fileId": "id1"})]


def test_create_folder_sets_properties():
    service = Service()
    fid = GoogleDrive(service).create_folder(".tmp-s1", "sessions", {"golfEtl": "1"})
    assert fid == "new"
    body = service.files().calls[0][1]["body"]
    assert body["parents"] == ["sessions"]
    assert body["appProperties"] == {"golfEtl": "1"}
