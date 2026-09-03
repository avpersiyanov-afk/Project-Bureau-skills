# -*- coding: utf-8 -*-

__title__ = u"Импорт\nиз Эксель"
__doc__ = u"Импорт правок из .xlsx обратно в модель: по столбцу Revit ID находит элементы и пишет параметры"
__author__ = "Pipers"

import traceback

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from pyrevit import revit, forms, script

from pbtools.schedule_excel import rows_to_model
from pbtools.xlsx_io import read_xlsx

doc = revit.doc


try:
    path = forms.pick_file(file_ext='xlsx')
    if not path:
        script.exit()

    rows = read_xlsx(path)
    if not rows:
        forms.alert(u"Файл пустой или не прочитался.", exitscript=True)

    with revit.Transaction(u"Импорт спецификации из Excel"):
        res = rows_to_model(doc, rows)

    parts = [
        u"Готово.",
        u"",
        u"Изменено значений: {}".format(res["changed"]),
    ]
    if res["unchanged"]:
        parts.append(u"Без изменений: {}".format(res["unchanged"]))
    if res["read_only"]:
        parts.append(u"Пропущено (только чтение / ссылка): {}".format(res["read_only"]))
    if res["no_param"]:
        parts.append(u"Параметр не найден у элемента: {}".format(res["no_param"]))
    if res["no_element"]:
        parts.append(u"Элемент не найден по ID: {}".format(res["no_element"]))
    if res["errors"]:
        parts.append(u"")
        parts.append(u"Ошибки записи ({}):".format(len(res["errors"])))
        parts.extend(res["errors"][:20])
        if len(res["errors"]) > 20:
            parts.append(u"… и ещё {}".format(len(res["errors"]) - 20))

    forms.alert(u"\n".join(parts))
except Exception:
    forms.alert(
        u"Сбой при импорте:\n\n{}".format(traceback.format_exc()),
        title=u"Импорт из Эксель"
    )
