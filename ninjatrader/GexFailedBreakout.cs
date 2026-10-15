// GEX failed breakouts for NinjaTrader 8 - Tom Hougaard's failed breakout at the walls printed by `python gex.py`.
// Same rules as gex/fakeouts.py, track.py and the TradingView indicator. Reads today's levels from
// history/levels_history.csv by itself, so there is nothing to paste.
//
// Install: copy this file to Documents\NinjaTrader 8\bin\Custom\Strategies, then NinjaScript Editor -> F5 (compile).
// Use: a 5-minute ES / MES / NQ / MNQ chart -> Strategies -> GexFailedBreakout -> account Sim101 -> Enabled.
// Orders only go to simulation or playback accounts; on any other account it only draws and alerts.
#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Windows;
using System.Windows.Media;
using System.Xml.Serialization;
using NinjaTrader.Cbi;
using NinjaTrader.Data;
using NinjaTrader.Gui;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

namespace NinjaTrader.NinjaScript.Strategies
{
    public class GexLevel
    {
        public string Name;
        public double Price;
        public bool Top;  // Call Wall / Expected Move High: faded from above (shorts)
        public bool Bottom;  // Put Wall / Expected Move Low: faded from below (longs)
        public int ShortPush = -1;  // bar that pushed above, waiting for a close back below (-1 = none)
        public double ShortHigh;
        public bool ShortDone;  // one setup per level and direction per day
        public int LongPush = -1;
        public double LongLow;
        public bool LongDone;

        public GexLevel(string name, double price)
        {
            Name = name;
            Price = price;
            Top = name.Contains("Call Wall") || name.Contains("Expected Move High");
            Bottom = name.Contains("Put Wall") || name.Contains("Expected Move Low");
        }
    }

    public class GexSignal
    {
        public bool IsShort;
        public string LevelName;
        public double Level;
        public double Entry;  // break of the signal bar's low (short) / high (long)
        public double Stop;  // beyond the furthest point of the push
        public double Target = double.NaN;  // next level in the trade's direction, NaN = none: trail
        public string TargetName = "";
        public double Close;  // signal bar close, for the regime check

        public double Risk { get { return Math.Abs(Stop - Entry); } }
        public double Reward { get { return double.IsNaN(Target) ? double.NaN : Math.Abs(Entry - Target); } }
        public double BeAt { get { return IsShort ? Entry - Risk : Entry + Risk; } }  // one risk in our favour: stop to entry
    }

    /// <summary>Today's levels from history/levels_history.csv, the same way gex/review.py reads them.</summary>
    public static class GexLevelsFile
    {
        // CSV column -> level name, in the order of gex/reactions.py LEVEL_FIELDS
        private static readonly string[,] Fields =
        {
            { "call_wall", "Call Wall" }, { "next_call_wall", "0DTE Call Wall" }, { "em_high", "Expected Move High" },
            { "flip", "Gamma Flip" }, { "magnet_1", "Magnet 1" }, { "magnet_2", "Magnet 2" }, { "magnet_3", "Magnet 3" },
            { "em_low", "Expected Move Low" }, { "next_put_wall", "0DTE Put Wall" }, { "put_wall", "Put Wall" },
        };

        /// <summary>Regular session the levels are for: same day until the 16:00 close, else the next weekday.</summary>
        public static DateTime SessionDate(DateTime asofNy)
        {
            DateTime day = asofNy.Date;
            if (asofNy.TimeOfDay >= new TimeSpan(16, 0, 0))
                day = day.AddDays(1);
            while (day.DayOfWeek == DayOfWeek.Saturday || day.DayOfWeek == DayOfWeek.Sunday)
                day = day.AddDays(1);
            return day;
        }

        /// <summary>Per session date: levels highest first (same price merged as "A + B") and the Gamma Flip (NaN if none).
        /// The last row per session wins.</summary>
        public static Dictionary<DateTime, KeyValuePair<List<GexLevel>, double>> Load(string path, string product)
        {
            var result = new Dictionary<DateTime, KeyValuePair<List<GexLevel>, double>>();
            string[] lines = File.ReadAllLines(path);
            if (lines.Length == 0)
                return result;
            string[] header = lines[0].Trim().Split(',');
            for (int n = 1; n < lines.Length; n++)
            {
                string[] cells = lines[n].Trim().Split(',');
                if (cells.Length != header.Length)
                    continue;
                var row = new Dictionary<string, string>();
                for (int i = 0; i < header.Length; i++)
                    row[header[i]] = cells[i];
                if (row["product"] != product)
                    continue;
                DateTime asof = DateTime.ParseExact(row["asof_ny"], "yyyy-MM-dd HH:mm", CultureInfo.InvariantCulture);
                var names = new Dictionary<double, string>();
                var order = new List<double>();
                double flip = double.NaN;
                for (int f = 0; f < Fields.GetLength(0); f++)
                {
                    string value;
                    if (!row.TryGetValue(Fields[f, 0], out value) || value == "")
                        continue;
                    double price = double.Parse(value, CultureInfo.InvariantCulture);
                    if (Fields[f, 0] == "flip")
                        flip = price;
                    if (names.ContainsKey(price))
                        names[price] = names[price] + " + " + Fields[f, 1];
                    else
                    {
                        names[price] = Fields[f, 1];
                        order.Add(price);
                    }
                }
                List<GexLevel> levels = order.OrderByDescending(p => p).Select(p => new GexLevel(names[p], p)).ToList();
                result[SessionDate(asof)] = new KeyValuePair<List<GexLevel>, double>(levels, flip);
            }
            return result;
        }
    }

    /// <summary>Failed breakouts bar by bar over one session (gex/fakeouts.py).</summary>
    public class GexDetector
    {
        public const double TolerancePct = 0.0002;  // within 0.02% of a level is a touch, not a push through
        public const int FailMinutes = 15;  // the close back inside must come within 15 minutes of the push

        private readonly List<GexLevel> levels;
        private readonly double tolerance;
        private readonly int failBars;
        private double prevClose = double.NaN;
        private int bar = -1;

        public GexDetector(IEnumerable<GexLevel> definitions, double sessionOpen, int barMinutes)
        {
            levels = definitions.Select(d => new GexLevel(d.Name, d.Price)).ToList();  // fresh state each session
            tolerance = sessionOpen * TolerancePct;
            failBars = Math.Max(1, (int)Math.Round(FailMinutes / (double)barMinutes));
        }

        public List<GexLevel> Levels { get { return levels; } }

        public List<GexSignal> OnBar(double open, double high, double low, double close)
        {
            bar++;
            double before = double.IsNaN(prevClose) ? open : prevClose;
            var signals = new List<GexSignal>();
            foreach (GexLevel lv in levels)
            {
                if (lv.Top && !lv.ShortDone)
                {
                    if (lv.ShortPush < 0 && before < lv.Price && high > lv.Price + tolerance)
                    {
                        lv.ShortPush = bar;
                        lv.ShortHigh = high;
                    }
                    else if (lv.ShortPush >= 0)
                        lv.ShortHigh = Math.Max(lv.ShortHigh, high);
                    if (lv.ShortPush >= 0)
                    {
                        if (close < lv.Price)
                        {
                            signals.Add(MakeSignal(true, lv, low, lv.ShortHigh, close));
                            lv.ShortDone = true;
                            lv.ShortPush = -1;
                        }
                        else if (bar - lv.ShortPush >= failBars - 1)
                            lv.ShortPush = -1;  // the breakout held: not a failure
                    }
                }
                if (lv.Bottom && !lv.LongDone)
                {
                    if (lv.LongPush < 0 && before > lv.Price && low < lv.Price - tolerance)
                    {
                        lv.LongPush = bar;
                        lv.LongLow = low;
                    }
                    else if (lv.LongPush >= 0)
                        lv.LongLow = Math.Min(lv.LongLow, low);
                    if (lv.LongPush >= 0)
                    {
                        if (close > lv.Price)
                        {
                            signals.Add(MakeSignal(false, lv, high, lv.LongLow, close));
                            lv.LongDone = true;
                            lv.LongPush = -1;
                        }
                        else if (bar - lv.LongPush >= failBars - 1)
                            lv.LongPush = -1;
                    }
                }
            }
            prevClose = close;
            return signals;
        }

        private GexSignal MakeSignal(bool isShort, GexLevel lv, double entry, double stop, double close)
        {
            var s = new GexSignal { IsShort = isShort, LevelName = lv.Name, Level = lv.Price, Entry = entry, Stop = stop, Close = close };
            foreach (GexLevel other in levels)
            {
                bool beyond = isShort ? other.Price < entry : other.Price > entry;
                if (beyond && (double.IsNaN(s.Target) || Math.Abs(other.Price - entry) < Math.Abs(s.Target - entry)))
                {
                    s.Target = other.Price;
                    s.TargetName = other.Name;
                }
            }
            return s;
        }

        /// <summary>"OK..." or "SKIP: why" (track.py setup_events).</summary>
        public static string Verdict(GexSignal s, double flip, out bool ok)
        {
            ok = false;
            if (!double.IsNaN(flip) && s.Close <= flip)
                return "SKIP: below Gamma Flip, walls break more often";
            if (!double.IsNaN(s.Reward) && s.Reward < s.Risk)
                return "SKIP: reward smaller than risk";
            ok = true;
            return double.IsNaN(flip) ? "OK? no Gamma Flip in the levels, check the regime" : "OK: positive gamma, walls tend to hold";
        }
    }

    public class GexFailedBreakout : Strategy
    {
        private static readonly TimeSpan Open930 = new TimeSpan(9, 30, 0);
        private static readonly TimeSpan Close1600 = new TimeSpan(16, 0, 0);

        private Dictionary<DateTime, KeyValuePair<List<GexLevel>, double>> levelsByDay;
        private TimeZoneInfo newYork;
        private string product;
        private bool ordersAllowed = true;

        private DateTime day = DateTime.MinValue;
        private GexDetector detector;  // null = no levels for this session
        private double flip = double.NaN;
        private int dayFirstBar;
        private int wins, losses, scratches;
        private double pnlPoints;

        private GexSignal trade;  // the one trade at a time
        private string tradeSignal;
        private int tradeBar, tradeCount;
        private bool filled, beMoved;
        private double fillPrice, exitPrice;
        private string exitName = "";
        private Order entryOrder;

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "Tom Hougaard's failed breakout at the GEX walls from python gex.py (levels_history.csv). Orders on Sim / Playback accounts only.";
                Name = "GexFailedBreakout";
                Calculate = Calculate.OnBarClose;  // closed bars only, like track.py
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.AllEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                IsFillLimitOnTouch = false;
                MaximumBarsLookBack = MaximumBarsLookBack.TwoHundredFiftySix;
                OrderFillResolution = OrderFillResolution.Standard;
                Slippage = 1;  // one tick against us on stop fills in backtests
                StartBehavior = StartBehavior.WaitUntilFlat;
                TimeInForce = TimeInForce.Gtc;
                TraceOrders = false;
                RealtimeErrorHandling = RealtimeErrorHandling.StopCancelClose;
                StopTargetHandling = StopTargetHandling.PerEntryExecution;
                BarsRequiredToTrade = 0;
                IsInstantiatedOnEachOptimizationIteration = true;

                LevelsFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Desktop), "gamma", "history", "levels_history.csv");
                Contracts = 1;
                CancelMinutes = 30;
                MaxLosses = 2;
            }
            else if (State == State.DataLoaded)
            {
                newYork = TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");
                product = Instrument.MasterInstrument.Name;
                if (product == "MES" || product == "MNQ")
                    product = product.Substring(1);  // micros use the full contract's levels
                LoadLevels();
            }
            else if (State == State.Realtime)
            {
                // NinjaTrader's own sim accounts, playback, or a broker connection in Simulation mode; never a Live connection
                ConnectOptions options = Account.Connection != null ? Account.Connection.Options : null;
                bool simConnection = options != null && options.Mode == NinjaTrader.Cbi.Mode.Simulation;
                ordersAllowed = Account.Provider == Provider.Simulator || Account.Provider == Provider.Playback || simConnection;
                Print("GEX: account " + Account.Name + ", provider " + Account.Provider
                    + (options != null ? ", connection " + options.Name + " (" + options.Mode + ")" : "")
                    + (ordersAllowed ? ": orders on" : ": NOT a simulation account, signals and alerts only"));
            }
        }

        protected override void OnBarUpdate()
        {
            if (BarsInProgress != 0 || CurrentBar < 1)
                return;
            if (BarsPeriod.BarsPeriodType != BarsPeriodType.Minute)
            {
                Draw.TextFixed(this, "gexWarn", "GEX failed breakouts: use a 5-minute chart", TextPosition.Center);
                return;
            }
            DateTime closeNy = TimeZoneInfo.ConvertTime(Time[0], Core.Globals.GeneralOptions.TimeZoneInfo, newYork);
            DateTime startNy = closeNy.AddMinutes(-BarsPeriod.Value);
            if (startNy.TimeOfDay < Open930 || startNy.TimeOfDay >= Close1600)
                return;
            if (startNy.Date != day)
                StartSession(startNy.Date);
            if (detector == null && State == State.Realtime && LoadLevels() && levelsByDay.ContainsKey(day))
                StartSession(day);  // gex.py logged today's levels after the strategy was switched on
            if (detector == null)
                return;
            bool lastBar = closeNy.TimeOfDay >= Close1600;

            ManageTrade(lastBar);
            foreach (GexSignal s in detector.OnBar(Open[0], High[0], Low[0], Close[0]))
                OnSignal(s);
            DrawSession();
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
                Print("GEX: cannot read " + LevelsFile + ": " + e.Message);
                return false;
            }
        }

        private void StartSession(DateTime date)
        {
            day = date;
            wins = losses = scratches = 0;
            pnlPoints = 0;
            KeyValuePair<List<GexLevel>, double> found;
            if (levelsByDay.TryGetValue(date, out found))
            {
                detector = new GexDetector(found.Key, Open[0], BarsPeriod.Value);
                flip = found.Value;
                dayFirstBar = CurrentBar;
            }
            else
            {
                detector = null;
                flip = double.NaN;
                Draw.TextFixed(this, "gexPanel", "GEX: no " + product + " levels for " + date.ToString("yyyy-MM-dd") + " in levels_history.csv. Run python gex.py.", TextPosition.TopRight);
            }
        }

        private void ManageTrade(bool lastBar)
        {
            if (trade == null || CurrentBar <= tradeBar)
                return;
            if (!filled)
            {
                bool stopFirst = trade.IsShort ? High[0] >= trade.Stop : Low[0] <= trade.Stop;
                if (entryOrder != null && (stopFirst || lastBar || CurrentBar - tradeBar >= CancelBars))
                    CancelOrder(entryOrder);  // OnOrderUpdate reports it
                return;
            }
            if (Position.MarketPosition == MarketPosition.Flat)
                return;
            if (lastBar)
            {
                if (trade.IsShort)
                    ExitShort(Position.Quantity, "Session end", tradeSignal);
                else
                    ExitLong(Position.Quantity, "Session end", tradeSignal);
                return;
            }
            bool reached = trade.IsShort ? Low[0] <= trade.BeAt : High[0] >= trade.BeAt;
            if (!beMoved && reached)
            {
                beMoved = true;
                bool stopValid = trade.IsShort ? Close[0] < trade.Entry : Close[0] > trade.Entry;
                if (stopValid)
                {
                    SetStopLoss(tradeSignal, CalculationMode.Price, trade.Entry, false);
                    Tell("move stop to " + Px(trade.Entry) + " (breakeven), the trade can no longer lose");
                }
                else if (trade.IsShort)  // price already back above the entry: get out at market
                    ExitShort(Position.Quantity, "Breakeven", tradeSignal);
                else
                    ExitLong(Position.Quantity, "Breakeven", tradeSignal);
            }
        }

        private void OnSignal(GexSignal s)
        {
            bool ok;
            string verdict = GexDetector.Verdict(s, flip, out ok);
            if (ok && trade != null)
            {
                ok = false;
                verdict = "SKIP: already in a trade";
            }
            else if (ok && losses >= MaxLosses)
            {
                ok = false;
                verdict = "SKIP: daily loss limit reached";
            }
            string head = (s.IsShort ? "FAILED BREAKOUT above " : "FAILED BREAKDOWN below ") + s.LevelName + " " + Px(s.Level);
            string order = (s.IsShort ? "SHORT below " : "LONG above ") + Px(s.Entry) + ", stop " + Px(s.Stop);
            string plan = double.IsNaN(s.Target) ? "no level beyond: trail the stop" : "target " + Px(s.Target) + " " + s.TargetName;
            string sizes = "risk " + Px(s.Risk) + (double.IsNaN(s.Reward) ? "" : ", reward " + Px(s.Reward)) + ". At " + Px(s.BeAt) + " move stop to " + Px(s.Entry);
            Brush color = ok ? Brushes.RoyalBlue : Brushes.Gray;
            Draw.Text(this, "gexSig" + CurrentBar + s.LevelName + s.IsShort, false, head + "\n" + order + "\n" + plan + "\n" + sizes + "\n" + verdict,
                0, s.IsShort ? High[0] : Low[0], s.IsShort ? 70 : -70, Brushes.White, new SimpleFont("Arial", 11), TextAlignment.Center, color, color, 60);
            if (!ok)
                return;
            bool live = State == State.Realtime;
            if (live && !ordersAllowed)
            {
                Tell(head + ": " + order + ", " + plan + ". Not a simulation account: no order sent.");
                return;
            }

            tradeCount++;
            tradeSignal = "GEX" + tradeCount;
            trade = s;
            tradeBar = CurrentBar;
            filled = beMoved = false;
            exitName = "";
            SetStopLoss(tradeSignal, CalculationMode.Price, s.Stop, false);
            if (!double.IsNaN(s.Target))
                SetProfitTarget(tradeSignal, CalculationMode.Price, s.Target);
            // a stop entry must sit beyond the market: if the bar closed on its extreme, one tick further
            double price = s.IsShort ? Math.Min(s.Entry, Close[0] - TickSize) : Math.Max(s.Entry, Close[0] + TickSize);
            entryOrder = s.IsShort
                ? EnterShortStopMarket(0, true, Contracts, price, tradeSignal)
                : EnterLongStopMarket(0, true, Contracts, price, tradeSignal);
            Tell(head + ": " + order + ", " + plan + ", " + sizes);
        }

        protected override void OnOrderUpdate(Order order, double limitPrice, double stopPrice, int quantity, int filledQuantity,
            double averageFillPrice, OrderState orderState, DateTime time, ErrorCode error, string nativeError)
        {
            if (trade == null || order.Name != tradeSignal)
                return;
            entryOrder = order;
            if (orderState == OrderState.Cancelled && filledQuantity == 0)
            {
                Draw.Text(this, "gexCancel" + CurrentBar, false, "CANCELLED: not filled", 0, trade.IsShort ? High[0] : Low[0],
                    trade.IsShort ? 20 : -20, Brushes.White, new SimpleFont("Arial", 10), TextAlignment.Center, Brushes.Gray, Brushes.Gray, 60);
                Tell((trade.IsShort ? "SHORT" : "LONG") + " order cancelled: not filled");
                trade = null;
                entryOrder = null;
            }
        }

        protected override void OnExecutionUpdate(Execution execution, string executionId, double price, int quantity,
            MarketPosition marketPosition, string orderId, DateTime time)
        {
            if (trade == null || execution.Order == null)
                return;
            if (execution.Order.Name == tradeSignal)
            {
                if (execution.Order.OrderState == OrderState.Filled)
                {
                    filled = true;
                    fillPrice = execution.Order.AverageFillPrice;
                    Tell((trade.IsShort ? "SHORT" : "LONG") + " filled at " + Px(fillPrice) + ": stop " + Px(trade.Stop)
                        + (double.IsNaN(trade.Target) ? ", no target: trail the stop" : ", target " + Px(trade.Target)));
                }
            }
            else
            {
                exitPrice = price;
                exitName = execution.Order.Name;
            }
        }

        protected override void OnPositionUpdate(Position position, double averagePrice, int quantity, MarketPosition marketPosition)
        {
            if (trade == null || !filled || marketPosition != MarketPosition.Flat)
                return;
            double points = trade.IsShort ? fillPrice - exitPrice : exitPrice - fillPrice;
            pnlPoints += points;
            if (points > 0)
                wins++;
            else if (points < 0)
                losses++;
            else
                scratches++;
            string result = exitName == "Profit target" ? "TARGET"
                : exitName == "Stop loss" ? (beMoved ? "SCRATCH at breakeven" : "STOPPED")
                : exitName == "Session end" || exitName == "Exit on session close" ? "FLAT at session end"
                : "EXIT (" + exitName + ")";
            Brush color = points > 0 ? Brushes.SeaGreen : points < 0 ? Brushes.Firebrick : Brushes.Gray;
            Draw.Text(this, "gexExit" + CurrentBar + tradeSignal, false, result + "\n" + Money(points), 0, trade.IsShort ? Low[0] : High[0],
                trade.IsShort ? -40 : 40, Brushes.White, new SimpleFont("Arial", 11), TextAlignment.Center, color, color, 70);
            Tell(result + " at " + Px(exitPrice) + ": " + Money(points)
                + (losses >= MaxLosses ? ". " + losses + " losses today: done for the day." : ""));
            trade = null;
            entryOrder = null;
        }

        private void DrawSession()
        {
            int ago = CurrentBar - dayFirstBar;
            string date = day.ToString("yyyyMMdd");
            foreach (GexLevel lv in detector.Levels)
            {
                Brush b = LevelBrush(lv.Name);
                DashStyleHelper dash = lv.Name.Contains("Expected") ? DashStyleHelper.Dash
                    : lv.Name.Contains("Magnet") && !lv.Name.Contains("Wall") ? DashStyleHelper.Dot : DashStyleHelper.Solid;
                Draw.Line(this, "gexL" + date + lv.Name, false, ago, lv.Price, 0, lv.Price, b, dash, lv.Name.Contains("0DTE") || dash != DashStyleHelper.Solid ? 1 : 2);
                Draw.Text(this, "gexT" + date + lv.Name, false, lv.Name + " " + Px(lv.Price), 0, lv.Price, 8, b,
                    new SimpleFont("Arial", 9), TextAlignment.Left, Brushes.Transparent, Brushes.Transparent, 0);
            }
            if (trade != null)
            {
                int since = CurrentBar - tradeBar;
                Draw.Line(this, "gexEntry" + tradeSignal, false, since, trade.Entry, 0, trade.Entry, Brushes.RoyalBlue, DashStyleHelper.Solid, 2);
                double stop = beMoved ? trade.Entry : trade.Stop;
                Draw.Line(this, "gexStop" + tradeSignal, false, since, stop, 0, stop, Brushes.Firebrick, DashStyleHelper.Solid, 2);
                if (!double.IsNaN(trade.Target))
                    Draw.Line(this, "gexTarget" + tradeSignal, false, since, trade.Target, 0, trade.Target, Brushes.SeaGreen, DashStyleHelper.Solid, 2);
            }

            string regime = double.IsNaN(flip) ? "No Gamma Flip in the levels"
                : Close[0] > flip ? "Above Gamma Flip " + Px(flip) + ": fades allowed" : "Below Gamma Flip " + Px(flip) + ": no fades";
            string status = losses >= MaxLosses ? "DONE FOR TODAY: " + losses + " losses" : "Waiting for a setup";
            if (trade != null)
                status = (filled ? "In trade: " : "Order: ") + (trade.IsShort ? "SHORT " : "LONG ") + Px(trade.Entry)
                    + ", stop " + Px(beMoved ? trade.Entry : trade.Stop) + (double.IsNaN(trade.Target) ? "" : ", target " + Px(trade.Target));
            Draw.TextFixed(this, "gexPanel", "GEX failed breakouts " + day.ToString("ddd MMM d") + "\n" + regime + "\n"
                + wins + " won, " + losses + " lost, " + scratches + " scratch: " + Money(pnlPoints) + "\n" + status
                + "\n" + (ordersAllowed ? "Orders on " + Account.Name : Account.Name + " is NOT a sim account: no orders"), TextPosition.TopRight);
        }

        private int CancelBars { get { return Math.Max(1, (int)Math.Round(CancelMinutes / (double)BarsPeriod.Value)); } }

        private static Brush LevelBrush(string name)
        {
            return name.Contains("Call") ? Brushes.MediumSeaGreen : name.Contains("Put") ? Brushes.IndianRed
                : name.Contains("Flip") ? Brushes.Gold : name.Contains("Expected") ? Brushes.MediumPurple : Brushes.Gray;
        }

        private string Px(double x)
        {
            return Instrument.MasterInstrument.RoundToTickSize(x).ToString("0.##", CultureInfo.InvariantCulture);
        }

        private string Money(double points)
        {
            double dollars = points * Instrument.MasterInstrument.PointValue * Contracts;
            return (points >= 0 ? "+" : "-") + Px(Math.Abs(points)) + " pts (" + (dollars >= 0 ? "+$" : "-$")
                + Math.Abs(dollars).ToString("0.##", CultureInfo.InvariantCulture) + ")";
        }

        private void Tell(string message)
        {
            string line = Instrument.MasterInstrument.Name + " " + message;
            Print(Time[0].ToString("yyyy-MM-dd HH:mm") + " " + line);
            if (State == State.Realtime)
                Alert("gex" + CurrentBar + message.GetHashCode(), Priority.High, line, NinjaTrader.Core.Globals.InstallDir + @"\sounds\Alert1.wav", 0, Brushes.Black, Brushes.White);
        }

        #region Properties
        [NinjaScriptProperty]
        [Display(Name = "Levels file", Description = "history/levels_history.csv written by python gex.py", Order = 1, GroupName = "GEX")]
        public string LevelsFile { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Contracts", Order = 2, GroupName = "GEX")]
        public int Contracts { get; set; }

        [NinjaScriptProperty]
        [Range(5, int.MaxValue)]
        [Display(Name = "Cancel an entry not filled after (minutes)", Order = 3, GroupName = "GEX")]
        public int CancelMinutes { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "Stop for the day after this many losses", Order = 4, GroupName = "GEX")]
        public int MaxLosses { get; set; }
        #endregion
    }
}
