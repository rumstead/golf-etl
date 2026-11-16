import json

from golf_etl.chain import CHAIN, LABELS, read_labels, write_chain

DTL = "down the line"


def swing(n, grade="good", view=DTL, **numbers):
    return {"swing": n, "grade": grade, "view": view} | numbers


def add_session(root, name, swings, club=None, captured="2026-11-08T16:13:00+00:00"):
    folder = root / name
    folder.mkdir(parents=True)
    row = {"session": name, "captured": captured, "venue": "sim", "per_swing": swings}
    if club:
        row["club"] = club
    (folder / "summary.json").write_text(json.dumps(row))


def chain(root):
    write_chain(root)
    return (root / CHAIN).read_text()


def table_row(text, name):
    return next(line for line in text.splitlines() if line.startswith(f"| {name} |"))


def test_a_measure_that_rises_with_strike_ranks_plus_one(tmp_path):
    swings = [
        swing(n, strike=s, head_impact_up=s / 10)
        for n, s in enumerate([1.0, 1.4, 1.2, 2.0, 1.7], 1)
    ]
    add_session(tmp_path, "2026-11-08-1613-aaaa", swings)
    row = table_row(chain(tmp_path), "Head impact up")
    assert row.startswith("| Head impact up |  |  | +1.00 | 5 (too few) | 1 |")


def test_values_are_taken_from_their_session_median(tmp_path):
    # the same swing pattern a whole unit higher on a second day pools as one pattern
    a = [swing(n, strike=s, head_impact_up=s) for n, s in enumerate([1.0, 1.5, 2.0, 2.5], 1)]
    b = [swing(n, strike=s, head_impact_up=s + 1) for n, s in enumerate([1.0, 1.5, 2.0, 2.5], 1)]
    add_session(tmp_path, "2026-11-08-1613-aaaa", a)
    add_session(tmp_path, "2026-11-14-1250-bbbb", b, captured="2026-11-14T12:50:00+00:00")
    assert "| Head impact up |  |  | +1.00 | 8 (too few) | 2 |" in chain(tmp_path)


def test_a_session_with_too_few_values_adds_none(tmp_path):
    add_session(tmp_path, "s1", [swing(n, strike=1.0 + n, tempo=3.0) for n in range(1, 4)])
    assert "| Tempo |" not in chain(tmp_path)


def test_face_on_swings_and_mixed_sessions_stay_out(tmp_path):
    swings = [swing(n, strike=1.0 + n / 10, head_impact_up=n) for n in range(1, 5)]
    swings += [swing(9, view="face-on", strike=9.0, head_impact_up=-9)]
    add_session(tmp_path, "s1", swings)
    add_session(tmp_path, "s2", swings, club="mixed")
    text = chain(tmp_path)
    assert "4 swings from 1 session." in text
    assert "Left out: s2 (mixed clubs)." in text


def test_each_club_gets_its_own_tables(tmp_path):
    swings = [swing(n, strike=1.0 + n / 10, head_impact_up=n) for n in range(1, 5)]
    add_session(tmp_path, "s1", swings)
    add_session(tmp_path, "s2", swings, club="driver")
    text = chain(tmp_path)
    assert "## 7 iron\n" in text and "## driver\n" in text


def test_spine_loss_is_address_minus_impact_and_sits_beside_hips(tmp_path):
    swings = [
        swing(n, strike=s, spine_address=36.0, spine_impact=36.0 - 2 * s, hips_impact_side=0.0)
        for n, s in enumerate([1.0, 1.5, 1.2, 2.0], 1)
    ]
    add_session(tmp_path, "s1", swings)
    text = chain(tmp_path)
    assert "| Spine loss |  |  | +1.00 | 4 (too few) | 1 |" in text
    names = [line.split(" | ")[0] for line in text.splitlines() if line.startswith("| ")]
    assert names.index("| Hips impact side") == names.index("| Spine loss") + 1


def test_strike_is_anchored_on_the_good_swings(tmp_path):
    # good swings strike at 1.0; the poor ones at the toe pull the session median out to 1.6
    strikes = [("good", 1.0), ("good", 1.0), ("good", 1.0), ("poor", 2.0), ("poor", 2.2)]
    strikes += [("poor", 1.8)]
    losses = [0.0, 0.1, 0.2, 3.0, 4.0, 2.0]
    swings = [
        swing(n, grade=g, strike=s, spine_address=36.0, spine_impact=36.0 - loss)
        for n, ((g, s), loss) in enumerate(zip(strikes, losses, strict=True), 1)
    ]
    add_session(tmp_path, "s1", swings)
    text = chain(tmp_path)
    assert "| kept | 3 (too few) | 3 (too few) |  | +0.00 |" in text
    assert "| lost | 3 (too few) |  | 3 (too few) | +1.00 |" in text


def test_strike_falls_back_to_the_session_median_without_two_good_swings(tmp_path):
    strikes = [("good", 1.0), ("poor", 1.5), ("poor", 2.0), ("poor", 2.5)]
    swings = [
        swing(n, grade=g, strike=s, spine_address=36.0, spine_impact=36.0 - s)
        for n, (g, s) in enumerate(strikes, 1)
    ]
    add_session(tmp_path, "s1", swings)
    text = chain(tmp_path)
    assert "| kept | 2 (too few) |" in text and "| -0.50 |" in text  # 1.0 and 1.5 against 1.75


def test_recent_sessions_get_their_own_tables(tmp_path):
    swings = [swing(n, strike=1.0 + n / 10, head_impact_up=n) for n in range(1, 5)]
    add_session(tmp_path, "old", swings, captured="2026-10-18T12:45:00+00:00")
    add_session(tmp_path, "new", swings, captured="2026-11-08T16:13:00+00:00")
    text = chain(tmp_path)
    assert "## 7 iron\n\n8 swings from 2 sessions." in text
    assert "## 7 iron, from 2026-10-31 on\n\n4 swings from 1 session." in text


def test_sessions_without_per_swing_numbers_are_named(tmp_path):
    folder = tmp_path / "s0"
    folder.mkdir()
    (folder / "summary.json").write_text(json.dumps({"captured": "2026-10-01T00:00:00+00:00"}))
    text = chain(tmp_path)
    assert "Left out: s0 (published before per-swing numbers)." in text
    assert "No session has per-swing numbers yet." in text


def test_ten_swings_read_without_the_too_few_mark(tmp_path):
    swings = [swing(n, strike=1.0 + n / 10, head_impact_up=n) for n in range(1, 11)]
    add_session(tmp_path, "s1", swings)
    assert "| Head impact up |  |  | +1.00 | 10 | 1 |" in chain(tmp_path)


def labeled_session(root, name="s1"):
    """Four swings whose posture and strike split two and two."""
    swings = [
        swing(
            n,
            grade="good" if n < 3 else "poor",
            strike=s,
            spine_address=36.0,
            spine_impact=36.0 - s,
        )
        for n, s in enumerate([1.0, 1.1, 2.0, 2.2], 1)
    ]
    add_session(root, name, swings)


def write_labels(root, text):
    (root / LABELS).write_text("session,swing,label,side,by,date,position\n" + text)


def test_labels_split_the_second_table_by_takeaway(tmp_path):
    # good swings anchor strike at 1.05; unlabeled swings never count as hanging
    labeled_session(tmp_path)
    write_labels(
        tmp_path, "s1,1,hanging,,owner,2026-11-16\ns1,swing-02,Set,inside,owner,2026-11-16\n"
    )
    takeaways = ("| hanging", "| level", "| not labeled")
    rows = [line for line in chain(tmp_path).splitlines() if line.startswith(takeaways)]
    assert rows[:6] == [  # then the same from 2026-10-31 on
        "| hanging | kept | 1 (too few) | 1 (too few) |  | -0.05 |",
        "| hanging | lost |  |  |  |  |",
        "| level or set | kept | 1 (too few) | 1 (too few) |  | +0.05 |",
        "| level or set | lost |  |  |  |  |",
        "| not labeled | kept |  |  |  |  |",
        "| not labeled | lost | 2 (too few) |  | 2 (too few) | +1.05 |",
    ]


def test_unreadable_swings_count_with_the_unlabeled(tmp_path):
    labeled_session(tmp_path)
    write_labels(tmp_path, "s1,3,unreadable,,owner,2026-11-16\n")
    assert "| not labeled | lost | 2 (too few) |" in chain(tmp_path)


def test_label_rows_that_cannot_be_used_are_named(tmp_path):
    labeled_session(tmp_path)
    write_labels(tmp_path, "s1,1,cupped,,owner,2026-11-16\ns1,x,set,,owner,2026-11-16\n")
    text = chain(tmp_path)
    assert (
        "Rows of takeaway-labels.csv not used: line 2 (label 'cupped'), line 3 (swing 'x')." in text
    )


def test_a_later_label_for_the_same_swing_wins(tmp_path):
    labels, unused = read_labels(
        _labels_file(tmp_path, "s1,1,set,,a,2026-11-16\ns1,1,hanging,,b,2026-11-17\n")
    )
    assert labels == {("s1", 1, "takeaway"): "hanging"} and unused == []


def _labels_file(root, text):
    write_labels(root, text)
    return root / LABELS


def test_swing_order_shows_warm_up(tmp_path):
    swings = [swing(n, strike=1.0, ball_speed=90.0 + n) for n in range(1, 6)]
    add_session(tmp_path, "s1", swings)
    assert "| Swing order | +1.00 | 5 (too few) |" in chain(tmp_path)


def test_takeaway_and_halfway_labels_rank_against_ball_speed(tmp_path):
    swings = [
        swing(n, strike=1.0, ball_speed=s) for n, s in enumerate([90.0, 95.0, 100.0, 104.0], 1)
    ]
    add_session(tmp_path, "s1", swings)
    write_labels(
        tmp_path,
        "s1,1,hanging,,owner,2026-11-16\ns1,2,hanging,,owner,2026-11-16\n"
        "s1,3,set,,owner,2026-11-16\ns1,4,level,,owner,2026-11-16\n",
    )
    with (tmp_path / LABELS).open("a") as fh:
        for n, label in ((1, "set"), (3, "hanging"), (4, "hanging")):
            fh.write(f"s1,{n},{label},,owner,2026-11-16,halfway\n")
    text = chain(tmp_path)
    assert "| Takeaway level or set | +0.89 | 4 (too few) |" in text
    assert "| Halfway level or set | -0.87 | 3 (too few) |" in text


def test_a_position_that_is_not_takeaway_or_halfway_is_not_used(tmp_path):
    labels, unused = read_labels(_labels_file(tmp_path, "s1,1,set,,a,2026-11-16,top\n"))
    assert labels == {} and unused == ["line 2 (position 'top')"]
