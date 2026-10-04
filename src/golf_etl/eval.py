"""Detection accuracy against hand-labeled impact times."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from golf_etl.config import Settings
from golf_etl.media import probe


@dataclass
class Score:
    true_pos: int = 0
    false_pos: int = 0
    false_neg: int = 0
    errors_ms: list[float] = field(default_factory=list)

    def add(self, other: "Score") -> None:
        self.true_pos += other.true_pos
        self.false_pos += other.false_pos
        self.false_neg += other.false_neg
        self.errors_ms += other.errors_ms

    @property
    def precision(self) -> float:
        found = self.true_pos + self.false_pos
        return self.true_pos / found if found else 1.0

    @property
    def recall(self) -> float:
        actual = self.true_pos + self.false_neg
        return self.true_pos / actual if actual else 1.0

    @property
    def mean_error_ms(self) -> float:
        return sum(self.errors_ms) / len(self.errors_ms) if self.errors_ms else 0.0

    def line(self, name: str) -> str:
        return (
            f"{name}: precision {self.precision:.2f} recall {self.recall:.2f} "
            f"tp {self.true_pos} fp {self.false_pos} fn {self.false_neg} "
            f"mean error {self.mean_error_ms:.0f}ms"
        )


def score(predicted: list[float], truth: list[float], tolerance_s: float) -> Score:
    """Greedy one-to-one match of each true impact to the closest unused prediction."""
    s = Score()
    unused = sorted(predicted)
    for t in sorted(truth):
        best = min(unused, key=lambda p: abs(p - t), default=None)
        if best is not None and abs(best - t) <= tolerance_s:
            unused.remove(best)
            s.true_pos += 1
            s.errors_ms.append(abs(best - t) * 1000)
        else:
            s.false_neg += 1
    s.false_pos = len(unused)
    return s


def run(
    labels_path: Path, cfg: Settings, detect_times: Callable[[Path, Settings], list[float]]
) -> tuple[Score, list[str]]:
    labels = yaml.safe_load(labels_path.read_text())
    tolerance_s = labels.get("tolerance_ms", 150) / 1000
    total, lines = Score(), []
    for entry in labels["videos"]:
        video = (labels_path.parent / entry["path"]).resolve()
        s = score(detect_times(video, cfg), [float(t) for t in entry["impacts"]], tolerance_s)
        total.add(s)
        lines.append(s.line(entry["path"]))
    lines.append(total.line("total"))
    return total, lines


def detected_impacts(video: Path, cfg: Settings) -> list[float]:
    from golf_etl.pipeline import detect_video

    return [s.impact_s for s in detect_video(video, probe(video), cfg).swings]
