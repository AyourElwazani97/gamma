// Print every failed breakout the C# rules find on the saved sessions, one line each, for comparison with Python.
using System;
using System.Globalization;
using System.IO;
using System.Linq;
using NinjaTrader.NinjaScript.Strategies;

static class Parity
{
    static readonly CultureInfo CI = CultureInfo.InvariantCulture;

    static void Main(string[] args)
    {
        string root = args[0];
        foreach (string product in new[] { "ES", "NQ" })
        {
            var byDay = GexLevelsFile.Load(Path.Combine(root, "history", "levels_history.csv"), product);
            foreach (var kv in byDay.OrderBy(k => k.Key))
            {
                string file = Path.Combine(root, "history", "bars", product + "_" + kv.Key.ToString("yyyy-MM-dd") + ".csv");
                if (!File.Exists(file))
                    continue;
                var bars = File.ReadAllLines(file).Skip(1).Select(l => l.Split(','))
                    .Select(c => new { T = DateTimeOffset.Parse(c[0], CI).DateTime, O = D(c[1]), H = D(c[2]), L = D(c[3]), C = D(c[4]) })
                    .Where(b => b.T.Date == kv.Key && b.T.TimeOfDay >= new TimeSpan(9, 30, 0) && b.T.TimeOfDay < new TimeSpan(16, 0, 0))
                    .ToList();
                if (bars.Count == 0)
                    continue;
                var detector = new GexDetector(kv.Value.Key, bars[0].O, 5);
                foreach (var b in bars)
                    foreach (GexSignal s in detector.OnBar(b.O, b.H, b.L, b.C))
                    {
                        bool ok;
                        string v = GexDetector.Verdict(s, kv.Value.Value, out ok);
                        string code = v.StartsWith("SKIP: below") ? "SKIP-flip" : v.StartsWith("SKIP: reward") ? "SKIP-rr" : "OK";
                        Console.WriteLine(string.Join("|", product, b.T.ToString("yyyy-MM-dd HH:mm"), s.IsShort ? "short" : "long", s.LevelName,
                            F(s.Level), F(s.Entry), F(s.Stop), double.IsNaN(s.Target) ? "" : F(s.Target), s.TargetName, code));
                    }
            }
        }
    }

    static double D(string s) { return double.Parse(s, CI); }
    static string F(double x) { return x.ToString("0.##", CI); }
}
