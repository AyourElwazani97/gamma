// Print what the footprint math gives on small hand-made cases, one line each, for tests/test_ninjatrader.py.
using System;
using System.Collections.Generic;
using System.Linq;
using NinjaTrader.NinjaScript.Indicators;

static class FootprintCheck
{
    static void Main()
    {
        double nan = double.NaN;
        Console.WriteLine("side " + string.Join(" ", new[] {
            FootprintMath.IsBuy(100.25, 100, 100.25, nan, false),  // at the ask
            FootprintMath.IsBuy(100, 100, 100.25, nan, true),  // at the bid
            FootprintMath.IsBuy(100.1, 100, 100.25, 100, false),  // between: uptick
            FootprintMath.IsBuy(100, 0, 0, 100.25, true),  // no quote: downtick
            FootprintMath.IsBuy(100, 0, 0, 100, false),  // unchanged: last side
            FootprintMath.IsBuy(100, 0, 0, nan, true),
        }.Select(b => b ? "T" : "F")));

        var day = new DateTime(2026, 10, 15, 9, 0, 0);
        var stamps = new[] { day.AddMinutes(31), day.AddMinutes(32), day.AddMinutes(33) };
        Func<int, DateTime> at = i => stamps[i];
        Console.WriteLine("bar " + string.Join(" ", new[] {
            FootprintMath.BarFor(3, at, day.AddSeconds(30 * 60 + 10)),
            FootprintMath.BarFor(3, at, day.AddMinutes(31)),
            FootprintMath.BarFor(3, at, day.AddMinutes(31).AddMilliseconds(1)),
            FootprintMath.BarFor(3, at, day.AddSeconds(32 * 60 + 30)),
            FootprintMath.BarFor(3, at, day.AddMinutes(33)),
            FootprintMath.BarFor(3, at, day.AddSeconds(33 * 60 + 1)),
            FootprintMath.BarFor(0, at, day),
        }));

        var bar = Make(new Dictionary<long, double[]> { { 100, new double[] { 5, 20 } }, { 101, new double[] { 70, 10 } },
            { 102, new double[] { 8, 30 } }, { 103, new double[] { 2, 0 } } });
        Console.WriteLine("rows1 " + Rows(bar, 1));
        Console.WriteLine("rows2 " + Rows(bar, 2));
        Console.WriteLine("gap " + Rows(Make(new Dictionary<long, double[]> { { 100, new double[] { 9, 0 } }, { 102, new double[] { 3, 0 } } }), 1));

        Console.WriteLine(Abs(Even(100, 110, 100, 101, 0, 60), 106, 1.5));  // heavy selling at the low, closed in the upper half
        Console.WriteLine(Abs(Even(100, 110, 100, 101, 0, 60), 105, 1.5));  // closed in the middle
        Console.WriteLine(Abs(Even(100, 110, 109, 110, 60, 0), 104, 1.5));  // heavy buying at the high, closed in the lower half
        Console.WriteLine(Abs(Even(100, 110, 100, 101, 100, 60), 106, 1.5));  // more buyers than sellers at the low
        Console.WriteLine(Abs(Even(100, 103, 100, 101, 0, 60), 103, 1.5));  // bar too small
        Console.WriteLine(Abs(Even(100, 110, 100, 101, 0, 60), 106, 3));  // not heavy enough for a 3x multiple

        Console.WriteLine("rowsize " + string.Join(" ", new[] { 20, 7, 3, 2, 1, 0.001 }.Select(px => FootprintMath.TicksPerRow(px, 12))));

        // Two bars, shaped like ES on Oct 15, 2026 at the 7719 0DTE Put Wall: a bar sells hard (delta -500 against an
        // average of 200) and closes near its low, the next dips 3 ticks lower, turns to buying and closes near its high
        FootprintBar sold = Even(100, 120, 100, 104, 0, 100);
        Console.WriteLine(Two(sold, 101, Even(97, 108, 105, 108, 50, 0), 107, 200));
        Console.WriteLine(Two(sold, 101, Even(94, 108, 105, 108, 50, 0), 107, 200));  // 6 ticks lower: more than a quarter of 20
        Console.WriteLine(Two(sold, 101, Even(97, 108, 105, 108, 50, 0), 107, 400));  // -500 is not 1.5x an average of 400
        Console.WriteLine(Two(sold, 101, Even(97, 108, 97, 100, 0, 50), 107, 200));  // the second bar kept selling
        Console.WriteLine(Two(sold, 115, Even(97, 108, 105, 108, 50, 0), 107, 200));  // the first bar closed in its upper half
        Console.WriteLine(Two(Even(100, 120, 116, 120, 100, 0), 119, Even(112, 123, 112, 115, 0, 50), 113, 200));  // the mirror at the high
    }

    static string Two(FootprintBar first, long firstClose, FootprintBar second, long secondClose, double averageDelta)
    {
        FootprintAbsorption a = first.TwoBar(firstClose, second, secondClose, averageDelta, 1.5);
        return a == null ? "two none" : "two " + (a.AtLow ? "low " : "high ") + a.FromTick + "-" + a.ToTick + " " + a.Effort;
    }

    static FootprintBar Make(Dictionary<long, double[]> cells)
    {
        var bar = new FootprintBar();
        foreach (var kv in cells)
        {
            bar.Add(kv.Key, kv.Value[0], true);
            bar.Add(kv.Key, kv.Value[1], false);
        }
        return bar;
    }

    /// <summary>10 bought and 10 sold at every price from low to high, plus extra buys / sells from `from` to `to`.</summary>
    static FootprintBar Even(long low, long high, long from, long to, double buys, double sells)
    {
        var bar = new FootprintBar();
        for (long t = low; t <= high; t++)
        {
            bar.Add(t, 10, true);
            bar.Add(t, 10, false);
            if (t >= from && t <= to)
            {
                bar.Add(t, buys, true);
                bar.Add(t, sells, false);
            }
        }
        return bar;
    }

    static string Rows(FootprintBar bar, int ticksPerRow)
    {
        return string.Join(" ", bar.Rows(ticksPerRow, 3).Select(r => r.Tick + ":" + r.Buy + "/" + r.Sell + ":"
            + (r.BuyImbalance ? "B" : "-") + (r.SellImbalance ? "S" : "-")));
    }

    static string Abs(FootprintBar bar, long closeTick, double multiple)
    {
        FootprintAbsorption a = bar.Absorption(closeTick, 2, multiple);
        return a == null ? "abs none" : "abs " + (a.AtLow ? "low " : "high ") + a.FromTick + "-" + a.ToTick + " " + a.Effort;
    }
}
