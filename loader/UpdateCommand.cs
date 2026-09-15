using System.Collections.Generic;
using System.Linq;
using Autodesk.Revit.Attributes;
using Autodesk.Revit.DB;
using Autodesk.Revit.UI;

namespace ProjectBureau.Loader
{
    /// <summary>
    /// Кнопка "Обновить с GitHub": перекачивает ProjectBureau.extension так
    /// же, как это происходит на старте Revit, но по требованию — не нужно
    /// закрывать и открывать Revit, чтобы проверить свежую правку скрипта.
    /// Существующие кнопки подхватывают новый код сразу же (script.py
    /// читается с диска при каждом клике). Новые *.pushbutton-папки Revit
    /// на лету на ленту не добавить — Revit API этого не позволяет; про них
    /// только предупреждаем.
    /// </summary>
    [Transaction(TransactionMode.Manual)]
    [Regeneration(RegenerationOption.Manual)]
    public class UpdateCommand : IExternalCommand
    {
        public Result Execute(ExternalCommandData commandData, ref string message, ElementSet elements)
        {
            var knownBefore = new HashSet<string>(App.ButtonsByClassName.Keys);

            bool updated = App.TryUpdateFromGitHub(App.ExtensionRoot);
            if (!updated)
            {
                TaskDialog.Show("ProjectBureau",
                    "Не удалось обновиться — нет сети или GitHub недоступен. " +
                    "Ничего не изменилось, работаем с тем, что было.");
                return Result.Succeeded;
            }

            var current = BundleScanner.DiscoverButtons(App.ExtensionRoot);

            foreach (var btn in current)
            {
                if (App.ButtonsByClassName.TryGetValue(btn.ClassName, out var pb))
                {
                    pb.ToolTip = btn.Tooltip;
                    pb.ItemText = btn.Title;
                }
            }

            var newOnes = current.Where(b => !knownBefore.Contains(b.ClassName)).ToList();

            string msg = "Скрипты обновлены с GitHub — новая логика уже действует, " +
                         "перезапускать Revit не нужно.";
            if (newOnes.Count > 0)
            {
                msg += "\n\nПоявились новые кнопки: " +
                       string.Join(", ", newOnes.Select(b => b.Title.Replace("\n", " "))) +
                       ". На ленте они появятся после перезапуска Revit.";
            }
            TaskDialog.Show("ProjectBureau", msg);
            return Result.Succeeded;
        }
    }
}
