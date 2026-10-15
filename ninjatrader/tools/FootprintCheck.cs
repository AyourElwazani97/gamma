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
        return a == null ? "abs none" : "abs " + (a.AtLow ? "low " : "high ") + a.FromTick + "-" + a.ToTick + " " + a.Volume;
    }
}
