using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Windows.Media.Imaging;
using Autodesk.Revit.UI;

namespace ProjectBureau.Loader
{
    /// <summary>
    /// Самостоятельный (без pyRevit) загрузчик панели «Project Bureau Skills»: на старте Revit
    /// сканирует ProjectBureau.tab\*.panel\*.pushbutton\{script.py,bundle.yaml},
    /// компилирует по одной тонкой IExternalCommand-обёртке на кнопку (см.
    /// BundleScanner) и строит ленту. Сами script.py не меняются и не
    /// компилируются — их выполняет PythonHost через pythonnet при клике.
    /// </summary>
    public class App : IExternalApplication
    {
        internal static string ExtensionRoot;
        internal static readonly Dictionary<string, PushButton> ButtonsByClassName =
            new Dictionary<string, PushButton>();

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

                TryUpdateFromGitHub(ExtensionRoot);

                var buttons = BundleScanner.DiscoverButtons(ExtensionRoot);
                if (buttons.Count == 0)
                {
                    TaskDialog.Show("ProjectBureau", "Кнопки не найдены в " + ExtensionRoot);
                    return Result.Failed;
                }

                string generatedDll = BundleScanner.CompileCommands(buttons);

                application.CreateRibbonTab("Project Bureau Skills");

                RibbonPanel servicePanel = application.CreateRibbonPanel("Project Bureau Skills", "Обновление");
                var updateData = new PushButtonData(
                    "Cmd_UpdateFromGitHub", "Обновить\nс GitHub",
                    typeof(App).Assembly.Location, "ProjectBureau.Loader.UpdateCommand")
                {
                    ToolTip = "Скачивает актуальные script.py с GitHub и сразу применяет их — " +
                               "без перезапуска Revit. Новые кнопки (если появились) покажутся " +
                               "только после перезапуска Revit.",
                };
                servicePanel.AddItem(updateData);

                RibbonPanel panel = application.CreateRibbonPanel("Project Bureau Skills", "Инструменты");

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
                        data.LargeImage = LoadIcon(btn.IconPath, 32);
                        data.Image = LoadIcon(btn.IconPath, 16);
                    }
                    if (panel.AddItem(data) is PushButton pb)
                    {
                        ButtonsByClassName[btn.ClassName] = pb;
                    }
                }

                return Result.Succeeded;
            }
            catch (Exception ex)
            {
                TaskDialog.Show("ProjectBureau", "Не удалось загрузить панель «Project Bureau Skills»:\n\n" + ex);
                return Result.Failed;
            }
        }

        public Result OnShutdown(UIControlledApplication application)
        {
            PythonHost.Shutdown();
            return Result.Succeeded;
        }

        /// <summary>
        /// Порядок поиска ProjectBureau.extension — без единой настройки
        /// должен работать и на машине разработчика, и на установленной
        /// через installer\Install-Target.ps1 копии на рабочих компьютерах:
        /// 1) явный override-файл рядом с DLL (на случай нестандартной
        ///    раскладки);
        /// 2) "соседняя" раскладка &lt;root&gt;\loader\ProjectBureau.Loader.dll
        ///    + &lt;root&gt;\ProjectBureau.extension — именно так раскладывает
        ///    установщик на рабочих компьютерах;
        /// 3) путь репозитория на машине разработчика (dotnet build кладёт
        ///    DLL в loader\bin\Release, поэтому вариант 2 для неё не подходит).
        /// </summary>
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

            string sibling = Path.GetFullPath(Path.Combine(asmDir, "..", "ProjectBureau.extension"));
            if (Directory.Exists(sibling))
                return sibling;

            return @"C:\project\Project-Bureau-skills\ProjectBureau.extension";
        }

        private const string RepoZipUrl =
            "https://codeload.github.com/avpersiyanov-afk/Project-Bureau-skills/zip/refs/heads/main";

        /// <summary>
        /// На рабочих компьютерах git не установлен, поэтому обновление —
        /// не "git pull", а скачивание zip-архива ветки main прямо с GitHub
        /// (codeload, без токена — репозиторий публичный) и подмена папки
        /// ProjectBureau.extension содержимым архива (кроме runtime\ —
        /// встроенного Python, которого в git нет). Любая ошибка (нет сети,
        /// GitHub недоступен) тихо игнорируется — Revit должен запуститься
        /// в любом случае с тем, что уже есть на диске.
        /// </summary>
        /// <returns>
        /// true, если скрипты реально подменены свежей копией с GitHub;
        /// false при любой проблеме (нет сети, GitHub недоступен и т.п.) —
        /// тогда работаем с тем, что уже лежит на диске. Вызывающий код сам
        /// решает, показывать ли это пользователю (на старте Revit —
        /// молча, из кнопки «Обновить» — явно).
        /// </returns>
        internal static bool TryUpdateFromGitHub(string extensionRoot)
        {
            string tempZip = null;
            string tempExtractDir = null;
            try
            {
                System.Net.ServicePointManager.SecurityProtocol |= System.Net.SecurityProtocolType.Tls12;

                tempZip = Path.Combine(Path.GetTempPath(), "ProjectBureau_update_" + Guid.NewGuid().ToString("N") + ".zip");
                tempExtractDir = Path.Combine(Path.GetTempPath(), "ProjectBureau_update_" + Guid.NewGuid().ToString("N"));

                using (var client = new System.Net.Http.HttpClient())
                {
                    client.Timeout = TimeSpan.FromSeconds(15);
                    client.DefaultRequestHeaders.UserAgent.ParseAdd("ProjectBureau.Loader");
                    // codeload.github.com стоит за CDN и может недолго отдавать
                    // закэшированный архив сразу после пуша — без этого
                    // "успешное" обновление иногда тихо подсовывает старую
                    // версию. no-cache + метка времени в URL заставляют брать
                    // актуальный main каждый раз.
                    client.DefaultRequestHeaders.CacheControl =
                        new System.Net.Http.Headers.CacheControlHeaderValue { NoCache = true, NoStore = true };
                    client.DefaultRequestHeaders.Pragma.Add(new System.Net.Http.Headers.NameValueHeaderValue("no-cache"));
                    string url = RepoZipUrl + "?nocache=" + DateTime.UtcNow.Ticks;
                    byte[] bytes = client.GetByteArrayAsync(url).ConfigureAwait(false).GetAwaiter().GetResult();
                    File.WriteAllBytes(tempZip, bytes);
                }

                Directory.CreateDirectory(tempExtractDir);
                ZipFile.ExtractToDirectory(tempZip, tempExtractDir);

                string extractedRepoRoot = Directory.GetDirectories(tempExtractDir).FirstOrDefault();
                string newExtension = extractedRepoRoot != null
                    ? Path.Combine(extractedRepoRoot, "ProjectBureau.extension")
                    : null;

                if (newExtension != null && Directory.Exists(newExtension))
                {
                    MirrorDirectory(newExtension, extensionRoot, skipTopLevelDirs: new[] { "runtime" });
                    return true;
                }
                return false;
            }
            catch
            {
                // Нет сети, GitHub недоступен, антивирус и т.п. — работаем
                // с тем, что уже лежит на диске.
                return false;
            }
            finally
            {
                try { if (tempZip != null && File.Exists(tempZip)) File.Delete(tempZip); } catch { }
                try { if (tempExtractDir != null && Directory.Exists(tempExtractDir)) Directory.Delete(tempExtractDir, true); } catch { }
            }
        }

        /// <summary>
        /// Зеркалирует source в dest: копирует новое/изменённое, удаляет то,
        /// чего больше нет в source. skipTopLevelDirs — папки прямо внутри
        /// dest, которые не трогаем (runtime\python — не из git, ставится
        /// установщиком один раз).
        /// </summary>
        private static void MirrorDirectory(string source, string dest, string[] skipTopLevelDirs)
        {
            Directory.CreateDirectory(dest);

            var sourceDirNames = new HashSet<string>(
                Directory.GetDirectories(source).Select(Path.GetFileName), StringComparer.OrdinalIgnoreCase);
            var sourceFileNames = new HashSet<string>(
                Directory.GetFiles(source).Select(Path.GetFileName), StringComparer.OrdinalIgnoreCase);

            foreach (var d in Directory.GetDirectories(dest))
            {
                string name = Path.GetFileName(d);
                if (skipTopLevelDirs.Contains(name, StringComparer.OrdinalIgnoreCase))
                    continue;
                if (!sourceDirNames.Contains(name))
                    Directory.Delete(d, true);
            }
            foreach (var f in Directory.GetFiles(dest))
            {
                if (!sourceFileNames.Contains(Path.GetFileName(f)))
                    File.Delete(f);
            }

            foreach (var sd in Directory.GetDirectories(source))
            {
                string name = Path.GetFileName(sd);
                if (skipTopLevelDirs.Contains(name, StringComparer.OrdinalIgnoreCase))
                    continue;
                MirrorDirectory(sd, Path.Combine(dest, name), Array.Empty<string>());
            }
            foreach (var sf in Directory.GetFiles(source))
            {
                File.Copy(sf, Path.Combine(dest, Path.GetFileName(sf)), true);
            }
        }

        /// <summary>
        /// Revit не масштабирует Image/LargeImage сам — ждёт готовую картинку
        /// нужного размера (16/32px), иначе показывает необрезанный кусок
        /// (например, угол) исходного файла. Икон-файлы в репозитории —
        /// 96x96, поэтому декодируем сразу в целевой размер.
        /// </summary>
        private static BitmapImage LoadIcon(string path, int pixelSize)
        {
            var bmp = new BitmapImage();
            bmp.BeginInit();
            bmp.UriSource = new Uri(path, UriKind.Absolute);
            bmp.DecodePixelWidth = pixelSize;
            bmp.DecodePixelHeight = pixelSize;
            bmp.CacheOption = BitmapCacheOption.OnLoad;
            bmp.EndInit();
            bmp.Freeze();
            return bmp;
        }
    }
}
