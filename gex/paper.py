"""Paper-trade a setup on the bars after its signal, with the same rules as the NinjaTrader strategy.

Stop entry at the setup's entry price, stop loss and target attached, stop to entry once price
goes one risk in our favour, entry cancelled if not filled within 30 minutes, flat at the last bar.
When one bar touches both the stop and the target, the stop counts (we can't know which came first).
"""
from dataclasses import dataclass

CANCEL_BARS = 6  # 30 minutes of 5-minute bars


@dataclass
class PaperTrade:
    outcome: str  # "target", "stop", "scratch" (stopped at entry), "close" (still open at the end), "cancelled"
    r: float  # result in multiples of the risk; 0 when cancelled


def simulate(setup, bars, cancel_bars=CANCEL_BARS):
    """Paper result of `setup` (FailedBreakout / SecondTouch) on one session's bars."""
    short = setup.direction == "short"
    risk = abs(setup.stop - setup.entry)
    be_at = setup.entry - risk if short else setup.entry + risk
    stop, moved, filled = setup.stop, False, False
    rest = bars[bars.index > setup.time]
    for k, (_, bar) in enumerate(rest.iterrows(), start=1):
        last = k == len(rest)
        if not filled:
            if bar.Low <= setup.entry if short else bar.High >= setup.entry:
                filled = True
            elif (bar.High >= stop if short else bar.Low <= stop) or k >= cancel_bars or last:
                return PaperTrade("cancelled", 0.0)
        if not filled:
            continue
        hit_stop = bar.High >= stop if short else bar.Low <= stop
        target = setup.target
        hit_target = target is not None and (bar.Low <= target if short else bar.High >= target)
        exit_price = stop if hit_stop else target if hit_target else bar.Close if last else None
        if exit_price is not None:
            points = setup.entry - exit_price if short else exit_price - setup.entry
            outcome = ("scratch" if moved else "stop") if hit_stop else "target" if hit_target else "close"
            return PaperTrade(outcome, points / risk if risk else 0.0)
        if not moved and (bar.Low <= be_at if short else bar.High >= be_at):
            moved, stop = True, setup.entry
    return PaperTrade("cancelled", 0.0)
