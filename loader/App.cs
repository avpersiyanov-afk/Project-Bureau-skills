using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Windows.Media.Imaging;
using Autodesk.Revit.UI;

namespace ProjectBureau.Loader
{
    /// <summary>
    /// Самостоятельный (без pyRevit) загрузчик панели «Бюро»: на старте Revit
    /// сканирует ProjectBureau.tab\*.panel\*.pushbutton\{script.py,bundle.yaml},
    /// компилирует по одной тонкой IExternalCommand-обёртке на кнопку (см.
    /// BundleScanner) и строит ленту. Сами script.py не меняются и не
    /// компилируются — их выполняет PythonHost через pythonnet при клике.
    /// </summary>
    public class App : IExternalApplication
    {
        internal static string ExtensionRoot;

        public Result OnStartup(UIControlledApplication application)
        {
            try
            {
                ExtensionRoot = ResolveExtensionRoot();
                if (!Directory.Exists(ExtensionRoot))
                {
                    TaskDialog.Show("ProjectBureau",
                        "Не найдена папка расширения:\n" + ExtensionRoot);
                    return Result.Failed;
                }

                TryGitPull(ExtensionRoot);

                var buttons = BundleScanner.DiscoverButtons(ExtensionRoot);
                if (buttons.Count == 0)
                {
                    TaskDialog.Show("ProjectBureau", "Кнопки не найдены в " + ExtensionRoot);
                    return Result.Failed;
                }

                string generatedDll = BundleScanner.CompileCommands(buttons);

                application.CreateRibbonTab("Бюро");
                RibbonPanel panel = application.CreateRibbonPanel("Бюро", "Инструменты");

                foreach (var btn in buttons)
                {
                    var data = new PushButtonData(
                        btn.ClassName, btn.Title, generatedDll,
                        "ProjectBureau.Generated." + btn.ClassName)
                    {
                        ToolTip = btn.Tooltip,
                    };
                    if (File.Exists(btn.IconPath))
                    {
                        var img = LoadIcon(btn.IconPath);
                        data.LargeImage = img;
                        data.Image = img;
                    }
                    panel.AddItem(data);
                }

                return Result.Succeeded;
            }
            catch (Exception ex)
            {
                TaskDialog.Show("ProjectBureau", "Не удалось загрузить панель «Бюро»:\n\n" + ex);
                return Result.Failed;
            }
        }

        public Result OnShutdown(UIControlledApplication application)
        {
            PythonHost.Shutdown();
            return Result.Succeeded;
        }

        private static string ResolveExtensionRoot()
        {
            string asmDir = Path.GetDirectoryName(Assembly.GetExecutingAssembly().Location);
            string overrideFile = Path.Combine(asmDir, "ProjectBureau.root.txt");
            if (File.Exists(overrideFile))
            {
                string p = File.ReadAllText(overrideFile).Trim();
                if (Directory.Exists(p))
                    return p;
            }
            return @"C:\project\Project-Bureau-skills\ProjectBureau.extension";
        }

        private static void TryGitPull(string extensionRoot)
        {
            try
            {
                string repoRoot = Directory.GetParent(extensionRoot).FullName;
                if (!Directory.Exists(Path.Combine(repoRoot, ".git")))
                    return;

                var psi = new ProcessStartInfo("git", "pull --ff-only")
                {
                    WorkingDirectory = repoRoot,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    CreateNoWindow = true,
                };
                using (var p = Process.Start(psi))
                {
                    p.WaitForExit(15000);
                }
            }
            catch
            {
                // Нет git в PATH, нет сети, конфликт слияния и т.п. — не
                // должно мешать запуску Revit, просто грузим то, что на диске.
            }
        }

        private static BitmapImage LoadIcon(string path)
        {
            var bmp = new BitmapImage();
            bmp.BeginInit();
            bmp.UriSource = new Uri(path, UriKind.Absolute);
            bmp.CacheOption = BitmapCacheOption.OnLoad;
            bmp.EndInit();
            bmp.Freeze();
            return bmp;
        }
    }
}
