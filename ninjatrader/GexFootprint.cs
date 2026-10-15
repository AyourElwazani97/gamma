// GEX footprint for NinjaTrader 8: inside every candle, how many contracts sellers hit at the bid (left number) and buyers
// lifted at the ask (right number) at each price, the bar's delta and volume, and absorption marks: heavy selling into the
// low that could not push price lower (buyers defending), or heavy buying into the high that could not lift it, inside
// one bar or over two (a bar sells hard, the next can't follow through and turns to buying).
// Absorption at a GEX level from history/levels_history.csv is alerted and logged to history/absorption.csv for review.py.
//
// Needs GexFailedBreakout.cs installed too (it reads the levels the same way).
// Install: copy this file to Documents\NinjaTrader 8\bin\Custom\Indicators, then NinjaScript Editor -> F5 (compile).
// Use: a 1- or 5-minute ES / MES / NQ / MNQ chart -> Indicators -> GexFootprint. Drag the time axis to widen the bars
// until the numbers show, and set the data series' Bar width to 1 so the candles don't hide them.
#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Chart;
using NinjaTrader.NinjaScript.Strategies;
#endregion

namespace NinjaTrader.NinjaScript.Indicators
{
    public class FootprintCell
    {
        public double Buy;  // lifted the ask
        public double Sell;  // hit the bid
    }

    public class FootprintRow
    {
        public long Tick;  // lowest price of the row, in ticks
        public double Buy;
        public double Sell;
        public bool BuyImbalance;  // buyers at this row >= ratio x sellers one row lower
        public bool SellImbalance;  // sellers at this row >= ratio x buyers one row higher

        public double Volume { get { return Buy + Sell; } }
    }

    public class FootprintAbsorption
    {
        public bool AtLow;  // sellers absorbed at the low (buyers defending); false = buyers absorbed at the high
        public int Bars = 1;  // 1 = inside one bar, 2 = the selling in one bar, the failure to follow through in the next
        public long FromTick;  // the extreme prices, in ticks
        public long ToTick;
        public double Effort;  // 1 bar: volume sold into the low / bought into the high; 2 bars: size of the first bar's delta
        public string LevelName = "";  // GEX level it happened at, "" = none
        public double Level = double.NaN;
    }

    /// <summary>One bar's trades by price (in ticks) and side.</summary>
    public class FootprintBar
    {
        public readonly SortedDictionary<long, FootprintCell> Cells = new SortedDictionary<long, FootprintCell>();
        public double Buy;
        public double Sell;

        public double Volume { get { return Buy + Sell; } }
        public double Delta { get { return Buy - Sell; } }
        public long LowTick { get { return Cells.Keys.First(); } }
        public long HighTick { get { return Cells.Keys.Last(); } }

        public void Add(long tick, double volume, bool buy)
        {
            FootprintCell cell;
            if (!Cells.TryGetValue(tick, out cell))
                Cells[tick] = cell = new FootprintCell();
            if (buy)
            {
                cell.Buy += volume;
                Buy += volume;
            }
            else
            {
                cell.Sell += volume;
                Sell += volume;
            }
        }

        /// <summary>Cells merged into rows of `ticksPerRow` prices, lowest first, with diagonal imbalances (buyers at a row
        /// against sellers one row lower, sellers against buyers one row higher; an empty neighbour counts as 1).</summary>
        public List<FootprintRow> Rows(int ticksPerRow, double imbalanceRatio)
        {
            var rows = new List<FootprintRow>();
            FootprintRow row = null;
            foreach (KeyValuePair<long, FootprintCell> kv in Cells)
            {
                long start = (long)Math.Floor(kv.Key / (double)ticksPerRow) * ticksPerRow;
                if (row == null || row.Tick != start)
                {
                    row = new FootprintRow { Tick = start };
                    rows.Add(row);
                }
                row.Buy += kv.Value.Buy;
                row.Sell += kv.Value.Sell;
            }
            for (int i = 0; i < rows.Count; i++)
            {
                if (i > 0)
                {
                    double sellBelow = rows[i - 1].Tick == rows[i].Tick - ticksPerRow ? rows[i - 1].Sell : 0;
                    rows[i].BuyImbalance = rows[i].Buy >= imbalanceRatio * Math.Max(sellBelow, 1);
                }
                if (i + 1 < rows.Count)
                {
                    double buyAbove = rows[i + 1].Tick == rows[i].Tick + ticksPerRow ? rows[i + 1].Buy : 0;
                    rows[i].SellImbalance = rows[i].Sell >= imbalanceRatio * Math.Max(buyAbove, 1);
                }
            }
            return rows;
        }

        /// <summary>Absorption on a finished bar. At the low: in its lowest `extremeTicks` prices sellers hit the bid at
        /// least `multiple` times the bar's average volume per price, and more than buyers lifted the ask there, yet the bar
        /// closed in its upper half: someone with limit orders soaked up the selling. At the high: the mirror image.
        /// Null when neither.</summary>
        public FootprintAbsorption Absorption(long closeTick, int extremeTicks, double multiple)
        {
            if (Cells.Count == 0)
                return null;
            long low = LowTick, high = HighTick, range = high - low;
            if (range < extremeTicks + 2)
                return null;  // too small a bar to close away from its extreme
            double heavy = multiple * Volume / (range + 1) * extremeTicks;
            double buys, sells;
            Sum(low, low + extremeTicks - 1, out buys, out sells);
            if (sells > buys && sells >= heavy && 2 * (closeTick - low) > range)
                return new FootprintAbsorption { AtLow = true, FromTick = low, ToTick = low + extremeTicks - 1, Effort = sells };
            Sum(high - extremeTicks + 1, high, out buys, out sells);
            if (buys > sells && buys >= heavy && 2 * (high - closeTick) > range)
                return new FootprintAbsorption { AtLow = false, FromTick = high - extremeTicks + 1, ToTick = high, Effort = buys };
            return null;
        }

        /// <summary>Absorption over two finished bars (this one first). At the low: this bar sold hard (its delta at least
        /// `deltaMultiple` times the average size of the delta of the bars before it) and closed in its lower half; the next
        /// went no more than a quarter of this bar's range below its low, its delta turned positive and it closed in its
        /// upper half, above this close: a big effort by the sellers, no result. At the high: the mirror image.
        /// Null when neither.</summary>
        public FootprintAbsorption TwoBar(long closeTick, FootprintBar next, long nextCloseTick, double averageDelta, double deltaMultiple)
        {
            if (Cells.Count == 0 || next.Cells.Count == 0 || averageDelta <= 0)
                return null;
            long low = LowTick, high = HighTick, range = high - low;
            long nextLow = next.LowTick, nextHigh = next.HighTick, nextRange = nextHigh - nextLow;
            double effort = deltaMultiple * averageDelta;
            if (-Delta >= effort && 2 * (closeTick - low) < range && 4 * (low - nextLow) <= range
                && next.Delta > 0 && 2 * (nextCloseTick - nextLow) > nextRange && nextCloseTick > closeTick)
                return new FootprintAbsorption { AtLow = true, Bars = 2, FromTick = Math.Min(low, nextLow), ToTick = low, Effort = -Delta };
            if (Delta >= effort && 2 * (high - closeTick) < range && 4 * (nextHigh - high) <= range
                && next.Delta < 0 && 2 * (nextHigh - nextCloseTick) > nextRange && nextCloseTick < closeTick)
                return new FootprintAbsorption { AtLow = false, Bars = 2, FromTick = high, ToTick = Math.Max(high, nextHigh), Effort = Delta };
            return null;
        }

        private void Sum(long fromTick, long toTick, out double buys, out double sells)
        {
            buys = sells = 0;
            for (long t = fromTick; t <= toTick; t++)
            {
                FootprintCell cell;
                if (Cells.TryGetValue(t, out cell))
                {
                    buys += cell.Buy;
                    sells += cell.Sell;
                }
            }
        }
    }

    public static class FootprintMath
    {
        // rows of 0.25, 0.5, 1, 2, 5, 10, ... points on ES / NQ
        private static readonly int[] RowSteps = { 1, 2, 4, 8, 20, 40, 80, 200, 400, 800, 2000 };

        /// <summary>True = a buyer lifted the ask, false = a seller hit the bid. Between the two, or without a quote: the
        /// direction of the last price change (tick rule), or the last side when the price did not change.</summary>
        public static bool IsBuy(double price, double bid, double ask, double lastPrice, bool lastBuy)
        {
            if (bid > 0 && ask > bid)
            {
                if (price >= ask)
                    return true;
                if (price <= bid)
                    return false;
            }
            if (!double.IsNaN(lastPrice) && price != lastPrice)
                return price > lastPrice;
            return lastBuy;
        }

        /// <summary>Index of the first bar stamped at or after `t` (time bars are stamped at their close, so it is the bar
        /// the trade belongs to), or -1 when `t` is after the last bar.</summary>
        public static int BarFor(int count, Func<int, DateTime> timeAt, DateTime t)
        {
            if (count == 0 || timeAt(count - 1) < t)
                return -1;
            int lo = 0, hi = count - 1;
            while (lo < hi)
            {
                int mid = (lo + hi) / 2;
                if (timeAt(mid) < t)
                    lo = mid + 1;
                else
                    hi = mid;
            }
            return lo;
        }

        /// <summary>Fewest ticks per row that make a row at least `minPixels` tall.</summary>
        public static int TicksPerRow(double pixelsPerTick, double minPixels)
        {
            foreach (int step in RowSteps)
                if (step * pixelsPerTick >= minPixels)
                    return step;
            return RowSteps[RowSteps.Length - 1];
        }
    }

    public class GexFootprint : Indicator
    {
        private const float MinRowPixels = 12;
        private const string LogHeader = "product,time_ny,minutes,bars,direction,level,level_price,low,high,close,effort,bar_volume,bar_delta";
        private static readonly CultureInfo CI = CultureInfo.InvariantCulture;
        private static readonly TimeSpan Open930 = new TimeSpan(9, 30, 0);
        private static readonly TimeSpan Close1600 = new TimeSpan(16, 0, 0);
        private static readonly object FileLock = new object();

        private struct Trade
        {
            public DateTime Time;
            public long Tick;
            public double Volume;
            public bool Buy;
        }

        private readonly object sync = new object();  // OnRender runs on the UI thread, the trades on the data thread
        private Dictionary<int, FootprintBar> bars;
        private Dictionary<int, List<FootprintAbsorption>> marks;  // by the bar that completed them
        private List<Trade> pending;  // trades newer than the last bar, waiting for it
        private double lastPrice = double.NaN;
        private bool lastBuy = true;

        private Dictionary<DateTime, KeyValuePair<List<GexLevel>, double>> levelsByDay;
        private TimeZoneInfo newYork;
        private string product;
        private string logPath;
        private HashSet<string> logged;

        private SharpDX.Direct2D1.Brush buyDx, sellDx, buyStrongDx, sellStrongDx, absorbDx, textDx;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Footprint: contracts sold at the bid x bought at the ask at each price, delta, and absorption at the GEX levels from python gex.py.";
                Name = "GexFootprint";
                Calculate = Calculate.OnEachTick;
                IsOverlay = true;
                DrawOnPricePanel = true;
                DisplayInDataBox = false;
                PaintPriceMarkers = false;
                IsSuspendedWhileInactive = true;

                TicksPerRow = 0;
                ImbalanceRatio = 3;
                ExtremeTicks = 2;
                AbsorbMultiple = 1.5;
                DeltaMultiple = 1.5;
                LevelsFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Desktop), "gamma", "history", "levels_history.csv");
                LogAbsorption = true;
                BuyBrush = Brushes.SeaGreen;
                SellBrush = Brushes.IndianRed;
                BuyStrongBrush = Brushes.DodgerBlue;
                SellStrongBrush = Brushes.Red;
                AbsorptionBrush = Brushes.Gold;
            }
            else if (State == State.Configure)
                AddDataSeries(BarsPeriodType.Tick, 1);
            else if (State == State.DataLoaded)
            {
                bars = new Dictionary<int, FootprintBar>();
                marks = new Dictionary<int, List<FootprintAbsorption>>();
                pending = new List<Trade>();
                newYork = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
                product = Instrument.MasterInstrument.Name;
                if (product == "MES" || product == "MNQ")
                    product = product.Substring(1);  // micros use the full contract's levels
                LoadLevels();
                logPath = Path.Combine(Path.GetDirectoryName(LevelsFile) ?? "", "absorption.csv");
                logged = new HashSet<string>();
                try
                {
                    if (File.Exists(logPath))
                        foreach (string line in File.ReadAllLines(logPath).Skip(1))
                        {
                            string[] c = line.Split(',');
                            if (c.Length > 4)
                                logged.Add(LogKey(c));
                        }
                }
                catch (Exception e)
                {
                    Print("GEX footprint: cannot read " + logPath + ": " + e.Message);
                }
            }
            else if (State == State.Terminated)
                DisposeDx();
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress == 1)
            {
                OnTrade();
                return;
            }
            lock (sync)
            {
                DateTime end = Time[0];
                int kept = 0;
                for (int i = 0; i < pending.Count; i++)
                {
                    Trade t = pending[i];
                    if (t.Time > end)
                        pending[kept++] = t;
                    else if (CurrentBar > 0)  // trades before the first bar's start would land in it
                        BarAt(CurrentBar).Add(t.Tick, t.Volume, t.Buy);
                }
                pending.RemoveRange(kept, pending.Count - kept);
            }
            if (IsFirstTickOfBar && CurrentBar > 1)
                Finish(CurrentBar - 1);  // every trade of the bar before is in
        }

        private void OnTrade()
        {
            int index = CurrentBars[1];
            double price = BarsArray[1].GetClose(index);
            double volume = BarsArray[1].GetVolume(index);
            bool buy = FootprintMath.IsBuy(price, BarsArray[1].GetBid(index), BarsArray[1].GetAsk(index), lastPrice, lastBuy);
            lastPrice = price;
            lastBuy = buy;
            var trade = new Trade { Time = BarsArray[1].GetTime(index), Tick = (long)Math.Round(price / TickSize), Volume = volume, Buy = buy };
            lock (sync)
            {
                NinjaTrader.Data.Bars primary = BarsArray[0];
                int bar = FootprintMath.BarFor(primary.Count, primary.GetTime, trade.Time);
                if (bar < 0)
                    pending.Add(trade);
                else if (bar > 0)
                    BarAt(bar).Add(trade.Tick, trade.Volume, trade.Buy);
            }
        }

        private FootprintBar BarAt(int index)
        {
            FootprintBar bar;
            if (!bars.TryGetValue(index, out bar))
                bars[index] = bar = new FootprintBar();
            return bar;
        }

        private void Finish(int index)
        {
            FootprintBar bar, before;
            var found = new List<FootprintAbsorption>();
            lock (sync)
            {
                if (!bars.TryGetValue(index, out bar) || bar.Cells.Count == 0)
                    return;
                FootprintAbsorption one = bar.Absorption(CloseTick(index), ExtremeTicks, AbsorbMultiple);
                if (one != null)
                    found.Add(one);
                if (bars.TryGetValue(index - 1, out before))
                {
                    FootprintAbsorption two = before.TwoBar(CloseTick(index - 1), bar, CloseTick(index), AverageDelta(index - 2), DeltaMultiple);
                    if (two != null)
                        found.Add(two);
                }
                if (found.Count == 0)
                    return;
                marks[index] = found;
            }
            if (BarsPeriod.BarsPeriodType == BarsPeriodType.Minute)
                foreach (FootprintAbsorption mark in found)
                    CheckLevel(index, bar, mark);
        }

        private long CloseTick(int index)
        {
            return (long)Math.Round(Bars.GetClose(index) / TickSize);
        }

        /// <summary>Average size of the delta of up to 20 bars ending at `last`; 0 with fewer than 5.</summary>
        private double AverageDelta(int last)
        {
            double sum = 0;
            int n = 0;
            for (int i = last; i > last - 20 && i > 0; i--)
            {
                FootprintBar b;
                if (bars.TryGetValue(i, out b) && b.Cells.Count > 0)
                {
                    sum += Math.Abs(b.Delta);
                    n++;
                }
            }
            return n >= 5 ? sum / n : 0;
        }

        /// <summary>Absorption at today's GEX level (regular session only): alert, and log it for review.py.
        /// `bar` is the bar that completed it.</summary>
        private void CheckLevel(int index, FootprintBar bar, FootprintAbsorption mark)
        {
            DateTime closeNy = TimeZoneInfo.ConvertTime(Bars.GetTime(index), NinjaTrader.Core.Globals.GeneralOptions.TimeZoneInfo, newYork);
            DateTime startNy = closeNy.AddMinutes(-BarsPeriod.Value);
            if (startNy.TimeOfDay < Open930 || closeNy.TimeOfDay > Close1600 || closeNy.Date != startNy.Date)
                return;
            KeyValuePair<List<GexLevel>, double> found;
            if (!levelsByDay.TryGetValue(startNy.Date, out found)
                && (State != State.Realtime || !LoadLevels() || !levelsByDay.TryGetValue(startNy.Date, out found)))
                return;  // levels logged after the indicator was loaded get picked up in real time
            double from = mark.FromTick * TickSize, to = mark.ToTick * TickSize;
            double tolerance = Math.Max(to * GexDetector.TolerancePct, 2 * TickSize);
            GexLevel nearest = null;
            double best = double.MaxValue;
            foreach (GexLevel lv in found.Key)
            {
                double away = lv.Price < from ? from - lv.Price : lv.Price > to ? lv.Price - to : 0;  // from the absorbed prices
                if (away <= tolerance && away < best)
                {
                    nearest = lv;
                    best = away;
                }
            }
            if (nearest == null)
                return;
            lock (sync)
            {
                mark.LevelName = nearest.Name;
                mark.Level = nearest.Price;
            }

            // the trade review.py paper-trades: long above the bar's high, stop under the absorbed low (mirror for shorts)
            double low = (mark.AtLow ? mark.FromTick : bar.LowTick) * TickSize, high = (mark.AtLow ? bar.HighTick : mark.ToTick) * TickSize;
            Log(string.Join(",", product, startNy.ToString("yyyy-MM-dd HH:mm", CI), BarsPeriod.Value.ToString(CI), mark.Bars.ToString(CI),
                mark.AtLow ? "long" : "short", nearest.Name, Num(nearest.Price), Num(low), Num(high), Num(Bars.GetClose(index)),
                Num(mark.Effort), Num(bar.Volume), Num(bar.Delta)));
            if (State != State.Realtime)
                return;
            string what;
            if (mark.Bars == 2)
                what = mark.AtLow
                    ? "the bar before sold hard (delta -" + Num(mark.Effort) + "), this one could not go much lower, turned to buying (delta " + Signed(bar.Delta) + ") and closed in its upper half"
                    : "the bar before bought hard (delta +" + Num(mark.Effort) + "), this one could not go much higher, turned to selling (delta " + Signed(bar.Delta) + ") and closed in its lower half";
            else
            {
                string zone = Px(from) + "-" + Px(to);
                what = mark.AtLow ? "sellers hit " + Num(mark.Effort) + " at " + zone + " but the bar closed in its upper half"
                                  : "buyers lifted " + Num(mark.Effort) + " at " + zone + " but the bar closed in its lower half";
            }
            string message = "ABSORPTION" + (mark.Bars == 2 ? " (2 bars)" : "") + " at " + nearest.Name + " " + Px(nearest.Price) + ": " + what
                + (mark.AtLow ? ", buyers are defending the level" : ", sellers are defending the level") + ". TEST signal, paper only.";
            Print(Time[0].ToString("yyyy-MM-dd HH:mm") + " " + Instrument.MasterInstrument.Name + " " + message);
            Alert("gexAbs" + index + "_" + mark.Bars, Priority.High, Instrument.MasterInstrument.Name + " " + message,
                NinjaTrader.Core.Globals.InstallDir + @"\sounds\Alert2.wav", 0, Brushes.Black, Brushes.Gold);
        }

        /// <summary>One row per product, bar, timeframe, pattern and direction.</summary>
        private static string LogKey(string[] c)
        {
            return string.Join("|", c[0], c[1], c[2], c[3], c[4]);
        }

        private void Log(string line)
        {
            if (!LogAbsorption)
                return;
            string key = LogKey(line.Split(','));
            lock (FileLock)
            {
                if (logged.Contains(key))
                    return;
                try
                {
                    File.AppendAllText(logPath, (File.Exists(logPath) ? "" : LogHeader + Environment.NewLine) + line + Environment.NewLine);
                    logged.Add(key);
                }
                catch (Exception e)
                {
                    Print("GEX footprint: cannot write " + logPath + ": " + e.Message);
                }
            }
        }

        private bool LoadLevels()
        {
            try
            {
                levelsByDay = GexLevelsFile.Load(LevelsFile, product);
                return true;
            }
            catch (Exception e)
            {
                if (levelsByDay == null)
                    levelsByDay = new Dictionary<DateTime, KeyValuePair<List<GexLevel>, double>>();
                Print("GEX footprint: cannot read " + LevelsFile + ": " + e.Message);
                return false;
            }
        }

        public override void OnRenderTargetChanged()
        {
            DisposeDx();
            if (RenderTarget == null)
                return;
            buyDx = BuyBrush.ToDxBrush(RenderTarget);
            sellDx = SellBrush.ToDxBrush(RenderTarget);
            buyStrongDx = BuyStrongBrush.ToDxBrush(RenderTarget);
            sellStrongDx = SellStrongBrush.ToDxBrush(RenderTarget);
            absorbDx = AbsorptionBrush.ToDxBrush(RenderTarget);
            textDx = (ChartControl != null ? ChartControl.Properties.ChartText : Brushes.Gray).ToDxBrush(RenderTarget);
        }

        private void DisposeDx()
        {
            foreach (SharpDX.Direct2D1.Brush b in new[] { buyDx, sellDx, buyStrongDx, sellStrongDx, absorbDx, textDx })
                if (b != null)
                    b.Dispose();
            buyDx = sellDx = buyStrongDx = sellStrongDx = absorbDx = textDx = null;
        }

        protected override void OnRender(ChartControl chartControl, ChartScale chartScale)
        {
            if (ChartBars == null || bars == null || buyDx == null || IsInHitTest)
                return;
            double span = chartScale.MaxValue - chartScale.MinValue;
            if (span <= 0)
                return;
            double pixelsPerTick = chartScale.Height * TickSize / span;
            int rowTicks = TicksPerRow > 0 ? TicksPerRow : FootprintMath.TicksPerRow(pixelsPerTick, MinRowPixels);
            float rowHeight = (float)(pixelsPerTick * rowTicks);
            float width = Math.Max(2f, (float)chartControl.Properties.BarDistance - 4f);
            float size = Math.Min(12f, Math.Max(7f, rowHeight - 3f));
            bool numbers = width >= size * 7 && rowHeight >= 9;
            bool totals = width >= 28;

            SharpDX.Direct2D1.AntialiasMode antialias = RenderTarget.AntialiasMode;
            RenderTarget.AntialiasMode = SharpDX.Direct2D1.AntialiasMode.Aliased;
            using (SharpDX.DirectWrite.TextFormat sellFont = Font(size, SharpDX.DirectWrite.TextAlignment.Trailing))
            using (SharpDX.DirectWrite.TextFormat buyFont = Font(size, SharpDX.DirectWrite.TextAlignment.Leading))
            using (SharpDX.DirectWrite.TextFormat labelFont = Font(10f, SharpDX.DirectWrite.TextAlignment.Center))
            {
                lock (sync)
                {
                    for (int i = Math.Max(1, ChartBars.FromIndex); i <= ChartBars.ToIndex; i++)
                    {
                        FootprintBar bar;
                        if (!bars.TryGetValue(i, out bar) || bar.Cells.Count == 0)
                            continue;
                        float x = chartControl.GetXByBarIndex(ChartBars, i);
                        float left = x - width / 2;
                        List<FootprintRow> rows = bar.Rows(rowTicks, ImbalanceRatio);
                        FootprintRow poc = rows[0];
                        foreach (FootprintRow r in rows)
                            if (r.Volume > poc.Volume)
                                poc = r;

                        foreach (FootprintRow r in rows)
                        {
                            SharpDX.RectangleF cell = Cell(chartScale, r.Tick, r.Tick + rowTicks - 1, left, width);
                            SharpDX.Direct2D1.Brush fill = r.Buy >= r.Sell ? buyDx : sellDx;
                            fill.Opacity = (float)(0.12 + 0.5 * r.Volume / Math.Max(poc.Volume, 1));
                            RenderTarget.FillRectangle(cell, fill);
                            if (!numbers)
                                continue;
                            float half = width / 2 - 3;
                            RenderTarget.DrawText(Num(r.Sell), sellFont, new SharpDX.RectangleF(left, cell.Top, half, cell.Height), r.SellImbalance ? sellStrongDx : textDx);
                            RenderTarget.DrawText(Num(r.Buy), buyFont, new SharpDX.RectangleF(x + 3, cell.Top, half, cell.Height), r.BuyImbalance ? buyStrongDx : textDx);
                        }
                        RenderTarget.DrawRectangle(Cell(chartScale, poc.Tick, poc.Tick + rowTicks - 1, left, width), textDx, 1f);

                        float below = chartScale.GetYByValue((bar.LowTick - 0.5) * TickSize) + 2;
                        if (totals)
                        {
                            RenderTarget.DrawText(Signed(bar.Delta), labelFont,
                                new SharpDX.RectangleF(x - 40, below, 80, 13), bar.Delta >= 0 ? buyStrongDx : sellStrongDx);
                            RenderTarget.DrawText(Num(bar.Volume), labelFont, new SharpDX.RectangleF(x - 40, below + 13, 80, 13), textDx);
                        }

                        List<FootprintAbsorption> found;
                        if (!marks.TryGetValue(i, out found))
                            continue;
                        float above = chartScale.GetYByValue((bar.HighTick + 0.5) * TickSize) - 15;
                        float under = below + (totals ? 26 : 0);
                        foreach (FootprintAbsorption mark in found)
                        {
                            float from = mark.Bars == 2 ? chartControl.GetXByBarIndex(ChartBars, i - 1) - width / 2 : left;  // over both bars
                            RenderTarget.DrawRectangle(Cell(chartScale, mark.FromTick, mark.ToTick, from - 2, x + width / 2 - from + 4), absorbDx, 2f);
                            string label = "ABSORB" + (mark.Bars == 2 ? " 2 bars" : "") + (mark.LevelName == "" ? "" : " " + mark.LevelName + " " + Px(mark.Level));
                            RenderTarget.DrawText(label, labelFont, new SharpDX.RectangleF(x - 110, mark.AtLow ? under : above, 220, 13), absorbDx);
                            if (mark.AtLow)
                                under += 13;
                            else
                                above -= 13;
                        }
                    }
                }
            }
            RenderTarget.AntialiasMode = antialias;
        }

        private SharpDX.RectangleF Cell(ChartScale scale, long fromTick, long toTick, float left, float width)
        {
            float top = scale.GetYByValue((toTick + 0.5) * TickSize);
            float bottom = scale.GetYByValue((fromTick - 0.5) * TickSize);
            return new SharpDX.RectangleF(left, top, width, Math.Max(1f, bottom - top));
        }

        private static SharpDX.DirectWrite.TextFormat Font(float size, SharpDX.DirectWrite.TextAlignment alignment)
        {
            return new SharpDX.DirectWrite.TextFormat(NinjaTrader.Core.Globals.DirectWriteFactory, "Arial", size)
            {
                TextAlignment = alignment,
                ParagraphAlignment = SharpDX.DirectWrite.ParagraphAlignment.Center,
                WordWrapping = SharpDX.DirectWrite.WordWrapping.NoWrap,
            };
        }

        private static string Num(double x)
        {
            return x.ToString("0.##", CI);
        }

        private static string Signed(double x)
        {
            return (x >= 0 ? "+" : "") + Num(x);
        }

        private string Px(double x)
        {
            return Instrument.MasterInstrument.RoundToTickSize(x).ToString("0.##", CI);
        }

        #region Properties
        [Range(0, int.MaxValue)]
        [Display(Name = "Ticks per row (0 = auto)", Description = "Prices merged into one row. Auto keeps rows tall enough to read.", Order = 1, GroupName = "Footprint")]
        public int TicksPerRow { get; set; }

        [Range(1, double.MaxValue)]
        [Display(Name = "Imbalance ratio", Description = "Buyers at a price >= this many times the sellers one row lower (or the reverse) prints the number in the strong color.", Order = 2, GroupName = "Footprint")]
        public double ImbalanceRatio { get; set; }

        [Range(1, 10)]
        [Display(Name = "Absorption: extreme ticks", Description = "How many prices at the bar's low / high count as its extreme.", Order = 3, GroupName = "Absorption")]
        public int ExtremeTicks { get; set; }

        [Range(0.1, double.MaxValue)]
        [Display(Name = "Absorption: volume multiple", Description = "Selling at the low (buying at the high) per price must reach this many times the bar's average volume per price.", Order = 4, GroupName = "Absorption")]
        public double AbsorbMultiple { get; set; }

        [Range(0.1, double.MaxValue)]
        [Display(Name = "Absorption 2 bars: delta multiple", Description = "The first bar's delta must reach this many times the average size of the delta of the 20 bars before it.", Order = 5, GroupName = "Absorption")]
        public double DeltaMultiple { get; set; }

        [Display(Name = "Levels file", Description = "history/levels_history.csv written by python gex.py", Order = 6, GroupName = "GEX")]
        public string LevelsFile { get; set; }

        [Display(Name = "Log absorption at GEX levels", Description = "Append them to absorption.csv next to the levels file, for review.py", Order = 7, GroupName = "GEX")]
        public bool LogAbsorption { get; set; }

        [XmlIgnore]
        [Display(Name = "Buyers", Order = 1, GroupName = "Colors")]
        public Brush BuyBrush { get; set; }

        [Browsable(false)]
        public string BuyBrushSerializable
        {
            get { return Serialize.BrushToString(BuyBrush); }
            set { BuyBrush = Serialize.StringToBrush(value); }
        }

        [XmlIgnore]
        [Display(Name = "Sellers", Order = 2, GroupName = "Colors")]
        public Brush SellBrush { get; set; }

        [Browsable(false)]
        public string SellBrushSerializable
        {
            get { return Serialize.BrushToString(SellBrush); }
            set { SellBrush = Serialize.StringToBrush(value); }
        }

        [XmlIgnore]
        [Display(Name = "Buy imbalance / positive delta", Order = 3, GroupName = "Colors")]
        public Brush BuyStrongBrush { get; set; }

        [Browsable(false)]
        public string BuyStrongBrushSerializable
        {
            get { return Serialize.BrushToString(BuyStrongBrush); }
            set { BuyStrongBrush = Serialize.StringToBrush(value); }
        }

        [XmlIgnore]
        [Display(Name = "Sell imbalance / negative delta", Order = 4, GroupName = "Colors")]
        public Brush SellStrongBrush { get; set; }

        [Browsable(false)]
        public string SellStrongBrushSerializable
        {
            get { return Serialize.BrushToString(SellStrongBrush); }
            set { SellStrongBrush = Serialize.StringToBrush(value); }
        }

        [XmlIgnore]
        [Display(Name = "Absorption", Order = 5, GroupName = "Colors")]
        public Brush AbsorptionBrush { get; set; }

        [Browsable(false)]
        public string AbsorptionBrushSerializable
        {
            get { return Serialize.BrushToString(AbsorptionBrush); }
            set { AbsorptionBrush = Serialize.StringToBrush(value); }
        }
        #endregion
    }
}
