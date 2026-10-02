import json

import pandas as pd

from gex.reactions import Reaction
from gex.track import flip_side, level_events, load_state, save_state


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
