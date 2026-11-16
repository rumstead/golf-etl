"""The club a session was hit with. The golfer picks it on the upload page, and it rides into
the inbox as a tag in the video's file name (`IMG_4881.mixed.MOV`), so it follows the video
through every move the poller makes. A name without a tag is a 7 iron."""

from pathlib import PurePosixPath

DEFAULT = "7 iron"
# the upload page's value, which is also the tag in the inbox file name, to the club
CLUBS = {"7iron": DEFAULT, "driver": "driver", "mixed": "mixed"}


def from_name(name: str) -> tuple[str, str]:
    """The source name without its club tag, and the club."""
    path = PurePosixPath(name)
    stem = PurePosixPath(path.stem)
    tag = stem.suffix.removeprefix(".")
    if tag in CLUBS and tag != "7iron":
        return stem.stem + path.suffix, CLUBS[tag]
    return name, DEFAULT
