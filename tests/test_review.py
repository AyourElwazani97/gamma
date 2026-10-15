from datetime import date

import pandas as pd
import pytest

from gex.reactions import levels_from_row
from gex.review import grade, session_bars, session_date, summarize

NY = "America/New_York"


def ts(text):
    return pd.Timestamp(text, tz=NY)


@pytest.mark.parametrize(
    "asof, expected",
    [
        ("2026-09-30 08:00", date(2026, 9, 30)),  # pre-market -> same day
        ("2026-09-30 12:00", date(2026, 9, 30)),  # intraday -> same day
        ("2026-09-30 18:39", date(2026, 10, 1)),  # after close -> next day
        ("2026-10-02 17:00", date(2026, 10, 5)),  # Friday evening -> Monday
        ("2026-10-03 10:00", date(2026, 10, 5)),  # Saturday -> Monday
    ],
)
def test_session_date(asof, expected):
    assert session_date(ts(asof)) == expected


def bars(day, rows):
    """rows: (HH:MM, open, high, low, close)"""
    index = pd.DatetimeIndex([ts(f"{day} {t}") for t, *_ in rows])
    return pd.DataFrame([r[1:] for r in rows], index=index, columns=["Open", "High", "Low", "Close"])


def test_session_bars_keep_regular_hours_only():
    b = bars(
        "2026-10-01",
        [("08:00", 1, 1, 1, 1), ("09:30", 2, 2, 2, 2), ("15:55", 3, 3, 3, 3), ("16:00", 4, 4, 4, 4)],
    )
    assert session_bars(b, date(2026, 10, 1))["Open"].tolist() == [2, 3]


def history_row(**overrides):
    row = {
        "asof_ny": "2026-09-30 18:39",
        "product": "ES",
        "regime": "negative",
        "flip": "7740",
        "call_wall": "7800",
        "put_wall": "7650",
        "em_low": "7660",
        "em_high": "7760",
    }
    row.update(overrides)
    return row


def test_grade_touch_and_hold():
    day_bars = bars(
        "2026-10-01",
        [("09:30", 7700, 7720, 7645, 7690), ("12:00", 7690, 7750, 7680, 7745), ("15:55", 7745, 7755, 7700, 7710)],
    )
    g = grade(history_row(), day_bars)
    assert g["date"] == date(2026, 10, 1)
    assert (g["open"], g["high"], g["low"], g["close"]) == (7700, 7755, 7645, 7710)
    assert g["range_vs_em"] == pytest.approx(110 / 100)
    assert g["closed_inside_em"] is True
    assert g["hit_put_wall"] is True and g["put_wall_held"] is True
    assert g["hit_call_wall"] is False and g["call_wall_held"] is None
    assert g["closed_above_flip"] is False


def test_grade_wall_broken():
    day_bars = bars("2026-10-01", [("09:30", 7700, 7710, 7600, 7620)])
    g = grade(history_row(), day_bars)
    assert g["hit_put_wall"] is True and g["put_wall_held"] is False
    assert g["closed_inside_em"] is False


def test_grade_none_without_session_data():
    assert grade(history_row(), bars("2026-10-02", [("10:00", 1, 1, 1, 1)])) is None


def test_grade_handles_missing_levels():
    g = grade(history_row(flip="", call_wall=""), bars("2026-10-01", [("09:30", 7700, 7710, 7690, 7700)]))
    assert g["hit_call_wall"] is None and g["closed_above_flip"] is None


def test_summarize_by_regime():
    graded = [
        {"regime": "positive", "range_vs_em": 0.6, "closed_inside_em": True,
         "hit_call_wall": True, "call_wall_held": True, "hit_put_wall": False, "put_wall_held": None},
        {"regime": "negative", "range_vs_em": 1.4, "closed_inside_em": False,
         "hit_call_wall": False, "call_wall_held": None, "hit_put_wall": True, "put_wall_held": False},
        {"regime": "negative", "range_vs_em": 1.0, "closed_inside_em": True,
         "hit_call_wall": False, "call_wall_held": None, "hit_put_wall": True, "put_wall_held": True},
    ]  # fmt: skip
    s = summarize(graded)
    assert s["days"] == 3
    assert s["closed_inside_em"] == 2
    assert s["avg_range_vs_em"] == {"positive": pytest.approx(0.6), "negative": pytest.approx(1.2)}
    assert (s["call_wall_touched"], s["call_wall_held"]) == (1, 1)
    assert (s["put_wall_touched"], s["put_wall_held"]) == (2, 1)


def test_review_main_end_to_end(tmp_path, monkeypatch, capsys):
    import gex.review as review

    history = tmp_path / "levels.csv"
    with open(history, "w", newline="") as f:
        f.write("asof_ny,product,regime,flip,call_wall,put_wall,em_low,em_high\n")
        f.write("2026-09-29 18:00,ES,positive,7700,7800,7650,7660,7760\n")  # older run, replaced below
        f.write("2026-09-30 08:00,ES,negative,7740,7800,7650,7660,7760\n")
    day_bars = bars("2026-09-30", [("09:30", 7700, 7720, 7645, 7690), ("15:55", 7690, 7755, 7680, 7710)])
    monkeypatch.setattr(review, "fetch_bars", lambda product, start, end: day_bars)

    assert review.main(["ES", "--history", str(history)], today=date(2026, 9, 30)) == 0
    out = capsys.readouterr().out
    assert "ES review: 1 session(s)" in out
    assert "negative" in out and "held" in out


def test_review_without_history(tmp_path, capsys):
    import gex.review as review

    assert review.main(["--history", str(tmp_path / "none.csv")]) == 1
    assert "No history" in capsys.readouterr().err


def test_grade_includes_level_reactions():
    day_bars = bars(
        "2026-10-01",
        [("09:30", 7700, 7720, 7690, 7700), ("09:35", 7700, 7702, 7649, 7660), ("09:40", 7660, 7690, 7655, 7688)],
    )
    g = grade(history_row(), day_bars)
    by_name = {r.name: r for r in g["reactions"]}
    assert by_name["Put Wall"].outcome == "held"
    assert by_name["Call Wall"].outcome == "not reached"


def test_scorecard_counts_merged_levels_and_groups_magnets():
    from gex.reactions import Reaction
    from gex.review import scorecard

    def r(name, outcome):
        return Reaction(name, 1.0, outcome != "not reached", None, None, outcome, 0.0, 0.0, 0)

    graded = [
        {"reactions": [r("Magnet 1", "held"), r("Magnet 2 + 0DTE Put Wall", "broke")]},
        {"reactions": [r("Magnet 3", "held"), r("0DTE Put Wall", "held")]},
    ]
    cards = scorecard(graded)
    assert cards["Magnet"] == {"held": 2, "broke": 1}
    assert cards["0DTE Put Wall"] == {"broke": 1, "held": 1}


def test_review_detail_and_charts(tmp_path, monkeypatch, capsys):
    import gex.review as review

    history = tmp_path / "levels.csv"
    history.write_text(
        "asof_ny,product,regime,flip,call_wall,put_wall,em_low,em_high\n"
        "2026-09-30 08:00,ES,negative,7740,7800,7650,7660,7760\n"
    )
    day_bars = bars("2026-09-30", [("09:30", 7700, 7720, 7645, 7690), ("09:35", 7690, 7755, 7680, 7710)])
    monkeypatch.setattr(review, "fetch_bars", lambda product, start, end: day_bars)

    charts = tmp_path / "charts"
    args = ["ES", "--history", str(history), "--detail", "--charts", str(charts)]
    assert review.main(args, today=date(2026, 9, 30)) == 0
    out = capsys.readouterr().out
    assert "Level scorecard" in out
    assert "Tested as" in out and "Put Wall" in out
    assert (charts / "ES_2026-09-30_review.png").stat().st_size > 10_000


def test_review_keeps_working_from_saved_bars_after_yahoo_drops_them(tmp_path, monkeypatch, capsys):
    import gex.review as review

    history = tmp_path / "levels.csv"
    history.write_text(
        "asof_ny,product,regime,flip,call_wall,put_wall,em_low,em_high\n"
        "2026-09-30 08:00,ES,negative,7740,7800,7650,7660,7760\n"
    )
    day_bars = bars("2026-09-30", [("09:30", 7700, 7720, 7645, 7690), ("15:55", 7690, 7755, 7680, 7710)])
    monkeypatch.setattr(review, "fetch_bars", lambda product, start, end: day_bars)
    assert review.main(["ES", "--history", str(history)], today=date(2026, 9, 30)) == 0
    assert (tmp_path / "bars" / "ES_2026-09-30.csv").exists()  # saved next to the history
    capsys.readouterr()

    def gone(product, start, end):
        raise AssertionError("should not download a saved session")

    monkeypatch.setattr(review, "fetch_bars", gone)
    assert review.main(["ES", "--history", str(history)], today=date(2026, 12, 31)) == 0  # > 60 days later
    assert "ES review: 1 session(s)" in capsys.readouterr().out


def paper(kind, r, outcome="target", positive_gamma=True, reward=8.0):
    from gex.fakeouts import FailedBreakout
    from gex.paper import PaperTrade

    t = ts("2026-10-15 10:00")
    setup = FailedBreakout("Call Wall", 100, "short", t, t, 100, 104, "Magnet 1", 100 - reward, positive_gamma)
    return kind, setup, PaperTrade(outcome, r)


def test_setup_scores_by_filter():
    from gex.review import FAILED, RETEST, setup_scores

    graded = [{"trades": [
        paper(FAILED, 2.0),
        paper(FAILED, -1.0, "stop", positive_gamma=False),
        paper(FAILED, 0.0, "cancelled"),  # never filled: not a trade
        paper(RETEST, -1.0, "stop", reward=2.0),  # reward 2 < risk 4
    ]}]  # fmt: skip
    scores = setup_scores(graded)
    assert scores[(FAILED, "all")] == (2, 1, 1, 1.0)
    assert scores[(FAILED, "OK: above flip, reward >= risk")] == (1, 1, 0, 2.0)
    assert scores[(FAILED, "below flip")] == (1, 0, 1, -1.0)
    assert scores[(RETEST, "all")] == (1, 0, 1, -1.0)
    assert scores[(RETEST, "OK: above flip, reward >= risk")] == (0, 0, 0, 0.0)


def test_grade_paper_trades_a_failed_breakout():
    from gex.review import FAILED

    b = bars("2026-09-30", [
        ("09:30", 7790, 7795, 7788, 7794),
        ("09:35", 7794, 7806, 7792, 7797),  # pushes 6 over the 7800 call wall, closes back under
        ("09:40", 7797, 7798, 7785, 7786),  # breaks the signal low 7792: short filled
        ("09:45", 7786, 7787, 7759, 7760),  # reaches the 7760 target
    ])  # fmt: skip
    g = grade(history_row(asof_ny="2026-09-30 08:00", flip="7700", em_low="7760"), b)
    [(kind, setup, trade)] = [t for t in g["trades"] if t[0] == FAILED]
    assert (setup.name, setup.direction, setup.entry, setup.stop) == ("Call Wall", "short", 7792, 7806)
    assert trade.outcome == "target" and trade.r == pytest.approx(32 / 14)


def test_review_prints_the_setup_scoreboard():
    from gex.review import FAILED, format_review

    g = grade(history_row(asof_ny="2026-09-30 08:00"), bars("2026-09-30", [("09:30", 7700, 7720, 7645, 7690)]))
    g["trades"] = [paper(FAILED, 2.0)]
    out = format_review("ES", [g])
    assert "Setup scoreboard" in out
    assert "Failed breakout" in out and "2nd touch of 0DTE wall" in out
    assert "+2.00" in out


def bars_with_volume(day, rows):
    """rows: (HH:MM, open, high, low, close, volume)"""
    index = pd.DatetimeIndex([ts(f"{day} {t}") for t, *_ in rows])
    return pd.DataFrame([r[1:] for r in rows], index=index, columns=["Open", "High", "Low", "Close", "Volume"])


def test_grade_marks_levels_near_the_prior_session_profile():
    prior = bars_with_volume("2026-09-29", [("10:00", 7800, 7800, 7800, 7800, 5000), ("10:05", 7790, 7791, 7789, 7790, 100)])
    today = bars_with_volume("2026-09-30", [("09:30", 7700, 7720, 7645, 7690, 1000)])
    g = grade(history_row(asof_ny="2026-09-30 08:00"), pd.concat([prior, today]))
    assert g["profile"].poc == 7800
    assert g["confluence"] == ["Call Wall"]  # 7800, on the prior POC; the others are far away


def test_review_compares_levels_near_the_profile_with_the_rest():
    from gex.review import format_review

    prior = bars_with_volume("2026-09-29", [("10:00", 7800, 7801, 7799, 7800, 5000)])
    today = bars_with_volume("2026-09-30", [("09:30", 7700, 7720, 7645, 7690, 1000), ("09:35", 7690, 7810, 7690, 7795, 900)])
    g = grade(history_row(asof_ny="2026-09-30 08:00"), pd.concat([prior, today]))
    out = format_review("ES", [g])
    assert "Levels near the prior session's volume profile" in out
    assert "near the profile" in out and "other levels" in out


def test_divergence_trades_get_their_own_scoreboard_row():
    from gex.review import FAILED, RETEST, RETEST_DIV, format_review, setup_scores

    graded = [{"trades": [paper(RETEST, -1.0, "stop"), paper(RETEST, 2.0), paper(RETEST_DIV, 2.0)]}]
    assert setup_scores(graded)[(RETEST_DIV, "all")] == (1, 1, 0, 2.0)
    g = grade(history_row(asof_ny="2026-09-30 08:00"), bars("2026-09-30", [("09:30", 7700, 7720, 7645, 7690)]))
    g["trades"] = graded[0]["trades"] + [paper(FAILED, 1.0)]
    assert "2nd touch + RSI divergence" in format_review("ES", [g])


def test_grade_checks_touch_odds_per_level():
    # Logged the evening before: odds run from the open (7700); 1 sd = 50 / 0.8 = 62.7 pts
    day_bars = bars("2026-10-01", [("09:30", 7700, 7720, 7645, 7690), ("15:55", 7690, 7755, 7680, 7710)])
    g = grade(history_row(), day_bars)
    odds = g["touch_odds"]  # highest level first: call wall 7800, EM high 7760, flip 7740, EM low 7660, put wall 7650
    assert [touched for _, touched in odds] == [False, False, True, True, True]
    assert odds[0][0] == pytest.approx(0.113, abs=0.001)


def test_touch_odds_start_at_the_snapshot_when_logged_mid_session():
    day_bars = bars("2026-10-01", [("09:30", 7700, 7720, 7645, 7690), ("12:00", 7690, 7750, 7680, 7745)])
    g = grade(history_row(asof_ny="2026-10-01 12:00"), day_bars)
    by_price = {price: touched for (_, price), (_, touched) in zip(levels_from_row(history_row()), g["touch_odds"])}
    assert by_price[7650.0] is False  # the 09:30 dip came before the snapshot


def test_touch_scores_by_predicted_bucket():
    from gex.review import touch_scores

    graded = [{"touch_odds": [(0.01, False), (0.03, True), (0.5, True)]}, {"touch_odds": [(0.45, False)]}]
    assert touch_scores(graded) == [
        ("0-5%", 2, pytest.approx(0.02), 0.5),
        ("40-60%", 2, pytest.approx(0.475), 0.5),
    ]


def test_review_prints_the_touch_odds_check():
    from gex.review import format_review

    g = grade(history_row(), bars("2026-10-01", [("09:30", 7700, 7720, 7645, 7690)]))
    assert "Touch odds check" in format_review("ES", [g])
