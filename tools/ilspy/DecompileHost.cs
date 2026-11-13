using System;
using System.IO;
using System.Reflection;
using ICSharpCode.Decompiler;
using ICSharpCode.Decompiler.CSharp;

class DecompileHost
{
    static int Main(string[] args)
    {
        if (args.Length < 2)
        {
            Console.Error.WriteLine("usage: DecompileHost.exe <assembly> <output.cs> [type-list]");
            return 1;
        }
        string asmPath = Path.GetFullPath(args[0]);
        string outPath = Path.GetFullPath(args[1]);

        var settings = new DecompilerSettings();
        settings.ThrowOnAssemblyResolveErrors = false;

        var decompiler = new CSharpDecompiler(asmPath, settings);
        string code = decompiler.DecompileWholeModuleAsString();
        File.WriteAllText(outPath, code);
        Console.WriteLine("decompiled " + asmPath + " -> " + outPath + " (" + code.Length + " chars)");
        return 0;
    }
}
