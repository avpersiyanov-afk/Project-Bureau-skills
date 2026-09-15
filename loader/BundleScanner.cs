using System;
using System.CodeDom.Compiler;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.CSharp;

namespace ProjectBureau.Loader
{
    internal class ButtonInfo
    {
        public string ClassName;
        public string Title;
        public string Tooltip;
        public string ScriptPath;
        public string IconPath;
    }

    /// <summary>
    /// Находит кнопки (ProjectBureau.tab\*.panel\*.pushbutton) и компилирует
    /// по одной тонкой IExternalCommand-обёртке на каждую — без ручного
    /// C#-кода на кнопку: добавить кнопку = добавить папку *.pushbutton со
    /// script.py и bundle.yaml, как раньше в pyRevit.
    /// </summary>
    internal static class BundleScanner
    {
        public static List<ButtonInfo> DiscoverButtons(string extensionRoot)
        {
            var result = new List<ButtonInfo>();

            string tabPath = Directory.GetDirectories(extensionRoot, "*.tab").FirstOrDefault();
            if (tabPath == null)
                return result;

            foreach (var panelPath in Directory.GetDirectories(tabPath, "*.panel"))
            {
                foreach (var btnPath in Directory.GetDirectories(panelPath, "*.pushbutton").OrderBy(p => p))
                {
                    string scriptPath = Path.Combine(btnPath, "script.py");
                    if (!File.Exists(scriptPath))
                        continue;

                    string bundlePath = Path.Combine(btnPath, "bundle.yaml");
                    string tooltip = File.Exists(bundlePath)
                        ? ParseTooltip(File.ReadAllText(bundlePath, Encoding.UTF8))
                        : "";
                    string title = ParseTitle(File.ReadAllText(scriptPath, Encoding.UTF8))
                        ?? Path.GetFileNameWithoutExtension(btnPath);

                    result.Add(new ButtonInfo
                    {
                        ClassName = "Cmd_" + Hash(scriptPath),
                        Title = title,
                        Tooltip = tooltip,
                        ScriptPath = scriptPath,
                        IconPath = Path.Combine(btnPath, "icon.png"),
                    });
                }
            }
            return result;
        }

        /// <summary>
        /// Компилирует по одному IExternalCommand на кнопку в общую DLL,
        /// кэшированную в %AppData%\ProjectBureau — пересборка только если
        /// набор/пути кнопок изменились (тот же приём, что у pyRevit: у него
        /// в %AppData%\pyRevit\&lt;год&gt; лежат такие же DLL с хэшем в имени).
        /// </summary>
        public static string CompileCommands(List<ButtonInfo> buttons)
        {
            string cacheDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                "ProjectBureau");
            Directory.CreateDirectory(cacheDir);
            string dllPath = Path.Combine(cacheDir, "ProjectBureau_generated.dll");
            string hashPath = dllPath + ".hash";

            string source = GenerateSource(buttons);
            string hash = Hash(source);

            if (File.Exists(dllPath) && File.Exists(hashPath) &&
                File.ReadAllText(hashPath).Trim() == hash)
            {
                return dllPath;
            }

            using (var provider = new CSharpCodeProvider())
            {
                var parameters = new CompilerParameters
                {
                    GenerateInMemory = false,
                    GenerateExecutable = false,
                    OutputAssembly = dllPath,
                    IncludeDebugInformation = false,
                };
                parameters.ReferencedAssemblies.Add("System.dll");
                parameters.ReferencedAssemblies.Add(typeof(App).Assembly.Location);
                parameters.ReferencedAssemblies.Add(
                    typeof(Autodesk.Revit.DB.Document).Assembly.Location); // RevitAPI.dll
                parameters.ReferencedAssemblies.Add(
                    typeof(Autodesk.Revit.UI.UIApplication).Assembly.Location); // RevitAPIUI.dll

                CompilerResults results;
                try
                {
                    results = provider.CompileAssemblyFromSource(parameters, source);
                }
                catch (IOException) when (File.Exists(dllPath))
                {
                    // DLL занята предыдущим запущенным Revit — используем ту,
                    // что уже есть, вместо падения загрузчика.
                    return dllPath;
                }

                if (results.Errors.HasErrors)
                {
                    var msg = string.Join("\n", results.Errors.Cast<CompilerError>().Select(e => e.ToString()));
                    throw new InvalidOperationException(
                        "Ошибка компиляции сгенерированных команд кнопок:\n" + msg);
                }
            }

            File.WriteAllText(hashPath, hash);
            return dllPath;
        }

        private static string GenerateSource(List<ButtonInfo> buttons)
        {
            var sb = new StringBuilder();
            sb.AppendLine("using Autodesk.Revit.Attributes;");
            sb.AppendLine("using Autodesk.Revit.DB;");
            sb.AppendLine("using Autodesk.Revit.UI;");
            sb.AppendLine("namespace ProjectBureau.Generated {");
            foreach (var b in buttons)
            {
                sb.AppendLine("[Transaction(TransactionMode.Manual)]");
                sb.AppendLine("[Regeneration(RegenerationOption.Manual)]");
                sb.AppendLine("public class " + b.ClassName + " : IExternalCommand {");
                sb.AppendLine("public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements) {");
                sb.AppendLine("return global::ProjectBureau.Loader.PythonHost.RunScript(@\"" +
                               b.ScriptPath.Replace("\"", "\"\"") + "\", commandData, ref message, elements);");
                sb.AppendLine("} }");
            }
            sb.AppendLine("}");
            return sb.ToString();
        }

        private static string Hash(string s)
        {
            using (var md5 = MD5.Create())
            {
                byte[] bytes = md5.ComputeHash(Encoding.UTF8.GetBytes(s));
                var sb = new StringBuilder();
                foreach (byte b in bytes)
                    sb.Append(b.ToString("x2"));
                return sb.ToString();
            }
        }

        private static readonly Regex TitleRegex = new Regex(
            "__title__\\s*=\\s*u?\"((?:[^\"\\\\]|\\\\.)*)\"", RegexOptions.Compiled);

        private static string ParseTitle(string scriptSource)
        {
            var m = TitleRegex.Match(scriptSource);
            if (!m.Success)
                return null;
            return m.Groups[1].Value.Replace("\\n", "\n").Replace("\\\"", "\"");
        }

        /// <summary>
        /// Мини-парсер bundle.yaml: в этом репозитории у pushbutton'ов
        /// встречается только "tooltip: однострочно" либо
        /// "tooltip: |\n  многострочный блок с отступом" — полноценный YAML
        /// не нужен.
        /// </summary>
        private static string ParseTooltip(string yaml)
        {
            var lines = yaml.Replace("\r\n", "\n").Split('\n');
            for (int i = 0; i < lines.Length; i++)
            {
                var m = Regex.Match(lines[i], @"^tooltip:\s*(.*)$");
                if (!m.Success)
                    continue;

                string rest = m.Groups[1].Value.Trim();
                if (rest != "|")
                    return rest.Trim('"');

                var block = new List<string>();
                int baseIndent = -1;
                for (int j = i + 1; j < lines.Length; j++)
                {
                    string bl = lines[j];
                    if (bl.Trim().Length == 0)
                    {
                        block.Add("");
                        continue;
                    }
                    int indent = bl.Length - bl.TrimStart(' ').Length;
                    if (baseIndent == -1)
                        baseIndent = indent;
                    if (indent < baseIndent)
                        break;
                    block.Add(bl.Substring(Math.Min(baseIndent, bl.Length)));
                }
                while (block.Count > 0 && block[block.Count - 1].Length == 0)
                    block.RemoveAt(block.Count - 1);
                return string.Join("\n", block);
            }
            return "";
        }
    }
}
