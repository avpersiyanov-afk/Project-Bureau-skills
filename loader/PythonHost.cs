using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using System.Windows.Input;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;
using Python.Runtime;

namespace ProjectBureau.Loader
{
    /// <summary>
    /// Запускает существующие script.py на встроенном CPython (через
    /// pythonnet) вместо pyRevit. lib\pyrevit (см. ProjectBureau.extension)
    /// первым встаёт в sys.path и подменяет собой настоящий pyrevit —
    /// поэтому сами script.py не меняются.
    /// </summary>
    public static class PythonHost
    {
        private static bool _initialized;
        private static readonly object InitLock = new object();

        private static void EnsureInitialized()
        {
            if (_initialized)
                return;

            lock (InitLock)
            {
                if (_initialized)
                    return;

                string pyHome = Path.Combine(App.ExtensionRoot, "runtime", "python");
                if (!Directory.Exists(pyHome))
                    throw new InvalidOperationException("Не найден встроенный Python: " + pyHome);

                string pyDll = FindPythonDll(pyHome);
                Runtime.PythonDLL = pyDll;

                Environment.SetEnvironmentVariable("PYTHONHOME", pyHome, EnvironmentVariableTarget.Process);
                Environment.SetEnvironmentVariable("PYTHONPATH", null, EnvironmentVariableTarget.Process);

                PythonEngine.Initialize();
                PythonEngine.BeginAllowThreads();

                _initialized = true;
            }
        }

        private static string FindPythonDll(string pyHome)
        {
            foreach (var file in Directory.GetFiles(pyHome, "python3*.dll"))
            {
                if (Regex.IsMatch(Path.GetFileName(file), @"^python3\d+\.dll$", RegexOptions.IgnoreCase))
                    return file;
            }
            throw new InvalidOperationException("pythonXY.dll не найден в " + pyHome);
        }

        public static Result RunScript(string scriptPath, ExternalCommandData commandData,
            ref string message, ElementSet elements)
        {
            try
            {
                EnsureInitialized();
            }
            catch (Exception ex)
            {
                TaskDialog.Show("ProjectBureau", "Не удалось запустить встроенный Python:\n\n" + ex);
                return Result.Failed;
            }

            UIApplication uiApp = commandData.Application;
            UIDocument uiDoc = uiApp.ActiveUIDocument;
            Document doc = uiDoc?.Document;
            bool shiftHeld = Keyboard.Modifiers == ModifierKeys.Shift;

            using (Py.GIL())
            {
                try
                {
                    dynamic sys = Py.Import("sys");
                    PrependPath(sys, Path.Combine(App.ExtensionRoot, "lib"));
                    PrependPath(sys, Path.GetDirectoryName(scriptPath));

                    dynamic pyrevit = Py.Import("pyrevit");
                    dynamic revitModule = Py.Import("pyrevit.revit");
                    revitModule.doc = doc.ToPython();
                    revitModule.uidoc = uiDoc.ToPython();
                    pyrevit.EXEC_PARAMS.config_mode = shiftHeld;

                    string code = File.ReadAllText(scriptPath, Encoding.UTF8);
                    using (var scope = Py.CreateScope())
                    {
                        scope.Set("__name__", "__main__");
                        scope.Set("__file__", scriptPath);
                        scope.Exec(code);
                    }
                }
                catch (PythonException pex)
                {
                    string formatted = pex.Format(); // настоящий Python-трейсбек
                    if (IsScriptExit(formatted))
                        return Result.Succeeded;

                    message = formatted;
                    TaskDialog.Show("ProjectBureau — ошибка", formatted);
                    return Result.Failed;
                }
            }

            return Result.Succeeded;
        }

        private static void PrependPath(dynamic sys, string dir)
        {
            if (string.IsNullOrEmpty(dir))
                return;
            dynamic path = sys.path;
            foreach (var existing in path)
            {
                if (string.Equals(existing.ToString(), dir, StringComparison.OrdinalIgnoreCase))
                    return;
            }
            path.insert(0, dir);
        }

        private static bool IsScriptExit(string formattedTraceback)
        {
            return formattedTraceback.IndexOf("ScriptExitException", StringComparison.Ordinal) >= 0;
        }

        /// <summary>
        /// Вызывается после успешного "Обновить с GitHub". script.py
        /// читается с диска на каждый клик (см. RunScript), но всё, что
        /// он импортирует (pbtools.*, pyrevit.*), Python держит в
        /// sys.modules и переиспользует до конца процесса Revit —
        /// подмена файлов на диске сама по себе на уже импортированные
        /// модули не действует. Без явного сброса кэша "успешное"
        /// обновление тихо продолжает работать со старым кодом до
        /// перезапуска Revit.
        /// </summary>
        public static void InvalidateModuleCache()
        {
            if (!_initialized)
                return; // Python ещё не стартовал в этой сессии — кэшировать нечего.

            using (Py.GIL())
            {
                dynamic sys = Py.Import("sys");
                dynamic modules = sys.modules;

                var stale = new List<string>();
                foreach (var key in modules)
                {
                    string name = key.ToString();
                    if (name == "pyrevit" || name.StartsWith("pyrevit.") ||
                        name == "pbtools" || name.StartsWith("pbtools."))
                    {
                        stale.Add(name);
                    }
                }
                foreach (var name in stale)
                {
                    modules.pop(name, null);
                }
            }
        }

        public static void Shutdown()
        {
            if (_initialized)
            {
                PythonEngine.Shutdown();
                _initialized = false;
            }
        }
    }
}
