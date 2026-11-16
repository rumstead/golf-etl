import pytest

from golf_etl.club import from_name


@pytest.mark.parametrize(
    ("name", "source", "club"),
    [
        ("IMG_4881.MOV", "IMG_4881.MOV", "7 iron"),
        ("IMG_4881.mixed.MOV", "IMG_4881.MOV", "mixed"),
        ("IMG_4881-2.driver.mov", "IMG_4881-2.mov", "driver"),
        ("IMG_4881.putter.MOV", "IMG_4881.putter.MOV", "7 iron"),
        ("IMG_4881.7iron.MOV", "IMG_4881.7iron.MOV", "7 iron"),
    ],
)
def test_club_comes_off_the_inbox_name(name, source, club):
    assert from_name(name) == (source, club)
