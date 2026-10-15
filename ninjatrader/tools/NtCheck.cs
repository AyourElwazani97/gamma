// Compile-check C# files against NinjaTrader 8's assemblies with NinjaTrader's own Roslyn.
// usage: NtCheck.exe <out file or -> <dll or exe> file1.cs [file2.cs ...]   (built by tests/test_ninjatrader.py)
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Microsoft.CodeAnalysis;
using Microsoft.CodeAnalysis.CSharp;

class NtCheck
{
    const string NtBin = @"C:\Program Files\NinjaTrader 8\bin";

    static int Main(string[] args)
    {
        AppDomain.CurrentDomain.AssemblyResolve += (s, e) =>
        {
            string path = Path.Combine(NtBin, new AssemblyName(e.Name).Name + ".dll");
            return File.Exists(path) ? Assembly.LoadFrom(path) : null;
        };
        return Run(args);
    }

    static int Run(string[] args)
    {
        string custom = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), @"NinjaTrader 8\bin\Custom");
        string fw = @"C:\Windows\Microsoft.NET\Framework64\v4.0.30319";
        var refPaths = new List<string> {
            fw + @"\mscorlib.dll", fw + @"\System.dll", fw + @"\System.Core.dll", fw + @"\System.Xml.dll", fw + @"\System.Xaml.dll",
            fw + @"\System.ComponentModel.DataAnnotations.dll", fw + @"\WPF\PresentationCore.dll", fw + @"\WPF\PresentationFramework.dll",
            fw + @"\WPF\WindowsBase.dll", fw + @"\netstandard.dll",
            NtBin + @"\NinjaTrader.Core.dll", NtBin + @"\NinjaTrader.Gui.dll", custom + @"\NinjaTrader.Custom.dll",
            NtBin + @"\SharpDX.dll", NtBin + @"\SharpDX.Direct2D1.dll", NtBin + @"\SharpDX.DXGI.dll" };  // chart rendering
        var refs = refPaths.Where(File.Exists).Select(p => (MetadataReference)MetadataReference.CreateFromFile(p)).ToList();
        var parse = CSharpParseOptions.Default.WithLanguageVersion(LanguageVersion.Latest);
        var trees = args.Skip(2).Select(f => CSharpSyntaxTree.ParseText(File.ReadAllText(f), parse, f)).ToList();
        var kind = args[1] == "exe" ? OutputKind.ConsoleApplication : OutputKind.DynamicallyLinkedLibrary;
        var comp = CSharpCompilation.Create(Path.GetFileNameWithoutExtension(args[0] == "-" ? "check.dll" : args[0]), trees, refs,
            new CSharpCompilationOptions(kind).WithPlatform(Platform.X64));
        using (var ms = new MemoryStream())
        {
            var res = comp.Emit(ms);
            int errors = 0;
            // CS1701/CS1702: harmless assembly version notes from NinjaTrader's own references.
            // CS0436: NinjaTrader.Custom.dll already holds the installed copy of the file being checked.
            var ignored = new[] { "CS1701", "CS1702", "CS0436" };
            foreach (var d in res.Diagnostics.Where(d => d.Severity >= DiagnosticSeverity.Warning && !ignored.Contains(d.Id)))
            {
                Console.WriteLine(d.ToString());
                if (d.Severity == DiagnosticSeverity.Error) errors++;
            }
            if (res.Success && args[0] != "-") File.WriteAllBytes(args[0], ms.ToArray());
            Console.WriteLine("SUCCESS=" + res.Success + " errors=" + errors);
            return res.Success ? 0 : 1;
        }
    }
}
