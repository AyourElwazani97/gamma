import json

import pandas as pd

from gex.fakeouts import FailedBreakout
from gex.reactions import Cross, Reaction
from gex.track import (
    append_refresh,
    closed_bars,
    cross_events,
    flip_side,
    level_events,
    load_state,
    refresh_events,
    save_state,
    setup_events,
)


def r(name, outcome, side="support", touch="09:35", bounce=0.0, through=0.0, price=100.0):
    first = None if touch is None else pd.Timestamp(f"2026-10-02 {touch}", tz="America/New_York")
    return Reaction(name, price, outcome != "not reached", first, side, outcome, bounce, through, 1)


def test_first_touch_emits_touch_event():
    events, state = level_events("ES", {}, [r("Put Wall", "stalled")], last=100.5)
    assert len(events) == 1
    assert "09:35 ES touched Put Wall 100" in events[0]
    assert "support" in events[0]
    assert state == {"Put Wall": "stalled"}


def test_resolution_emits_held_or_broke_once():
    events, state = level_events("ES", {"Put Wall": "stalled"}, [r("Put Wall", "held", bounce=9.5)], last=110)
    assert len(events) == 1 and "HELD" in events[0] and "9.5" in events[0]
    again, _ = level_events("ES", state, [r("Put Wall", "held", bounce=12)], last=112)
    assert again == []


def test_touch_and_resolution_in_same_poll():
    events, state = level_events("NQ", {}, [r("Call Wall", "broke", side="resistance", through=40)], last=100)
    assert len(events) == 2
    assert "touched Call Wall" in events[0] and "BROKE" in events[1]
    assert state["Call Wall"] == "broke"


def test_not_reached_is_silent():
    events, state = level_events("ES", {}, [r("Call Wall", "not reached", side=None, touch=None)], last=100)
    assert events == [] and state == {"Call Wall": "not reached"}


def test_flip_side():
    assert flip_side(101.0, 100.0) == "above"
    assert flip_side(99.0, 100.0) == "below"
    assert flip_side(99.0, None) is None


def test_state_round_trip(tmp_path):
    path = tmp_path / "state.json"
    assert load_state(path) == {}
    save_state(path, {"ES": {"levels": {"Put Wall": "held"}, "flip": "below"}})
    assert load_state(path) == {"ES": {"levels": {"Put Wall": "held"}, "flip": "below"}}
    assert json.loads(path.read_text())["ES"]["flip"] == "below"


def test_nq_prices_keep_their_decimals():
    events, _ = level_events("NQ", {}, [r("Magnet", "stalled", price=30846.5)], last=30809.75)
    assert "Magnet 30846.5" in events[0]
    assert "last 30809.75" in events[0]


def fb(direction="short", positive_gamma=True, target=("Magnet 3", 7849.5)):
    t = pd.Timestamp("2026-10-12 09:40", tz="America/New_York")
    entry, stop = (7863.25, 7876.0) if direction == "short" else (7860.0, 7845.0)
    return FailedBreakout(
        "0DTE Call Wall", 7874.5, direction, t, t, entry, stop, target[0], target[1], positive_gamma
    )


def test_failed_breakout_alert_gives_the_trade_plan_once():
    events, seen = setup_events("ES", [], [fb()])
    assert len(events) == 1
    line = events[0]
    assert "09:40 ES FAILED BREAKOUT above 0DTE Call Wall 7874.5" in line
    assert "SHORT below 7863.25" in line and "stop above 7876" in line
    assert "target Magnet 3 7849.5" in line
    assert "risk 12.75" in line and "reward 13.75" in line
    assert "at 7850.5 move stop to 7863.25" in line  # one risk in our favour -> stop to entry
    assert "OK" in line
    again, _ = setup_events("ES", seen, [fb()])
    assert again == []


def test_failed_breakdown_below_flip_says_skip():
    [line], _ = setup_events("ES", [], [fb("long", positive_gamma=False, target=(None, None))])
    assert "FAILED BREAKDOWN below 0DTE Call Wall" in line
    assert "LONG above 7860" in line and "stop below 7845" in line
    assert "no level beyond" in line
    assert "SKIP" in line


def test_reward_smaller_than_risk_says_skip():
    [line], _ = setup_events("ES", [], [fb(target=("Magnet 3", 7858.25))])  # reward 5 vs risk 12.75
    assert "SKIP" in line and "reward smaller than risk" in line


def cx(name, direction, at, close, count, price=30719.25):
    return Cross(name, price, direction, pd.Timestamp(f"2026-10-13 {at}", tz="America/New_York"), close, count)


def test_crossing_back_through_a_level_is_alerted_once():
    first = r("Expected Move Low", "broke", side="resistance", price=30719.25)  # the BROKE line covers cross #1
    crosses = [cx("Expected Move Low", "above", "09:35", 30758, 1), cx("Expected Move Low", "below", "10:50", 30696.5, 2)]
    [line], counts = cross_events("NQ", {}, crosses, [first], last=30717)
    assert "10:50 NQ closed back BELOW Expected Move Low 30719.25 at 30696.5" in line
    assert "cross #2 today" in line and "last 30717" in line
    assert counts == {"Expected Move Low": 2}
    again, _ = cross_events("NQ", counts, crosses, [first], last=30717)
    assert again == []


def test_first_cross_is_alerted_when_the_first_touch_held():
    held = r("Put Wall", "held", side="support", price=30858.75)
    [line], _ = cross_events("NQ", {}, [cx("Put Wall", "below", "13:05", 30840, 1, price=30858.75)], [held], last=30835)
    assert "13:05 NQ closed BELOW Put Wall 30858.75 at 30840 (cross #1 today" in line


def test_gamma_flip_crosses_are_left_to_the_flip_alert():
    crosses = [cx("Gamma Flip", "above", "11:00", 7830, 1, price=7819.25), cx("Gamma Flip + Magnet 1", "below", "11:05", 7810, 1)]
    events, _ = cross_events("ES", {}, crosses, [], last=7810)
    assert events == []


def test_closed_bars_drop_the_forming_bar_even_with_a_delayed_feed():
    # at 11:37 Yahoo's newest ES bar was 11:25 and its close was still changing
    index = pd.date_range("2026-10-13 11:15", periods=3, freq="5min", tz="America/New_York")
    bars = pd.DataFrame({"Close": [7845.5, 7842.5, 7841.25]}, index=index)
    assert list(closed_bars(bars)["Close"]) == [7845.5, 7842.5]


def second_touch(positive_gamma=False, target=("Magnet 2", 7749.0)):
    from gex.retests import SecondTouch

    t = pd.Timestamp("2026-10-15 10:40", tz="America/New_York")
    return SecondTouch("Magnet 3 + 0DTE Put Wall", 7719, "long", t, t, 7726, 7716.5, target[0], target[1], positive_gamma)


def test_second_touch_alert_is_a_test_setup_in_any_regime():
    [line], seen = setup_events("ES", [], [second_touch()])
    assert "10:40 ES 2ND TOUCH of Magnet 3 + 0DTE Put Wall 7719" in line
    assert "LONG above 7726" in line and "stop below 7716.5" in line
    assert "TEST setup, not proven: paper-trade only (below Gamma Flip)" in line
    again, _ = setup_events("ES", seen, [second_touch()])
    assert again == []


def test_second_touch_and_failed_breakout_at_one_level_both_alert():
    breakdown = fb("long")
    breakdown.name = "Magnet 3 + 0DTE Put Wall"
    lines, _ = setup_events("ES", [], [breakdown, second_touch()])
    assert len(lines) == 2


def test_second_touch_with_reward_smaller_than_risk_says_skip():
    [line], _ = setup_events("ES", [], [second_touch(target=("Magnet 2", 7730.0))])  # reward 4 vs risk 9.5
    assert "SKIP: reward smaller than risk" in line


def test_second_touch_alert_says_if_rsi_diverged():
    s = second_touch()
    s.rsi_first, s.rsi_second, s.divergence = 31.2, 38.4, True
    [line], _ = setup_events("ES", [], [s])
    assert "with RSI divergence (RSI 31 -> 38)" in line
    s.divergence = False
    [line], _ = setup_events("ES", [], [s])
    assert "no RSI divergence (RSI 31 -> 38)" in line
    s.rsi_first = s.rsi_second = s.divergence = None
    [line], _ = setup_events("ES", [], [s])
    assert "RSI" not in line


NOW = pd.Timestamp("2026-10-15 13:00", tz="America/New_York")


def test_refreshed_flip_on_the_other_side_of_price_is_alerted():
    # Morning flip 30800 had NQ below it; the refreshed flip dropped under price
    lines, st = refresh_events("NQ", {}, {"flip": 30100.0, "total": -2.0}, 30800.0, 30166.0, NOW)
    assert len(lines) == 1
    assert "NQ GEX REFRESH: Gamma Flip 30100 (morning 30800)" in lines[0]
    assert "price 30166 ABOVE" in lines[0] and "POSITIVE gamma" in lines[0]
    assert st == {"flip": 30100.0, "side": "above"}


def test_refresh_is_quiet_while_nothing_changes():
    st = {"flip": 30100.0, "side": "above"}
    assert refresh_events("NQ", st, {"flip": 30110.0, "total": -2.0}, 30800.0, 30170.0, NOW) == ([], st)


def test_first_refresh_close_to_the_morning_flip_is_quiet():
    lines, st = refresh_events("ES", {}, {"flip": 7796.0, "total": 3.0}, 7798.25, 7760.0, NOW)
    assert lines == [] and st == {"flip": 7798.25, "side": "below"}  # 2 points: same picture as the morning


def test_flip_that_moves_without_crossing_price_is_alerted_once():
    st = {"flip": 30800.0, "side": "below"}
    lines, st = refresh_events("NQ", st, {"flip": 30500.0, "total": -9.0}, 30800.0, 30166.0, NOW)
    assert len(lines) == 1 and "still BELOW" in lines[0] and "NEGATIVE gamma" in lines[0]
    assert refresh_events("NQ", st, {"flip": 30495.0, "total": -9.0}, 30800.0, 30166.0, NOW)[0] == []


def test_refresh_without_a_flip_is_quiet():
    st = {"flip": 30800.0, "side": "below"}
    assert refresh_events("NQ", st, {"flip": None, "total": -9.0}, 30800.0, 30166.0, NOW) == ([], st)


def test_refreshes_are_logged_for_review(tmp_path):
    path = tmp_path / "gex_refresh.csv"
    append_refresh(path, NOW, "NQ", 30166.0, {"flip": 30100.0, "total": -2.0})
    append_refresh(path, NOW, "ES", 7760.0, {"flip": None, "total": 3.0})
    lines = path.read_text().splitlines()
    assert lines[0] == "time_ny,product,fut_last,flip,total_gex_bn"
    assert lines[1] == "2026-10-15 13:00,NQ,30166,30100,-2.0"
    assert lines[2] == "2026-10-15 13:00,ES,7760,,3.0"
