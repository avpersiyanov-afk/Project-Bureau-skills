# -*- coding: utf-8 -*-

__title__ = u"Экспорт\nв Эксель"
__doc__ = u"Экспорт спецификации в .xlsx: первый столбец Revit ID, дальше параметры по столбцам"
__author__ = "Pipers"

import os
import traceback

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import ViewSchedule
from pyrevit import revit, forms, script

from pbtools.schedule_excel import (
    list_schedules, merge_export, schedule_to_rows, schedule_name,
)
from pbtools.xlsx_io import read_xlsx, read_xlsx_col_widths, write_xlsx

doc = revit.doc


def pick_schedule():
    av = doc.ActiveView
    if isinstance(av, ViewSchedule):
        try:
            if not av.IsTemplate and not av.Definition.IsKeySchedule:
                return av
        except Exception:
            pass

    scheds = list_schedules(doc)
    if not scheds:
        forms.alert(u"В проекте нет подходящих спецификаций.", exitscript=True)

    by_name = {}
    for v in scheds:
        by_name[schedule_name(v)] = v

    chosen = forms.SelectFromList.show(
        sorted(by_name.keys()),
        title=u"Какую спецификацию выгрузить",
        button_name=u"Выгрузить",
        multiselect=False,
    )
    if not chosen:
        script.exit()
    return by_name[chosen]


try:
    sched = pick_schedule()
    name = schedule_name(sched)

    rows, n_els, n_cols, col_widths = schedule_to_rows(doc, sched)
    if n_els == 0:
        forms.alert(
            u"В спецификации «{}» нет элементов-экземпляров для выгрузки.".format(name),
            exitscript=True
        )

    path = forms.save_file(file_ext='xlsx', default_name=name)
    if not path:
        script.exit()

    merge_line = u""
    if os.path.isfile(path):
        try:
            existing = read_xlsx(path)
            ex_widths = read_xlsx_col_widths(path)
            rows, col_widths, st = merge_export(rows, col_widths, existing, ex_widths)
            if st["merged"]:
                merge_line = (
                    u"\nОбновлён существующий файл: совмещено строк {}, "
                    u"добавлено новых элементов {}, сохранено доп. столбцов {}, "
                    u"ручных строк {}".format(
                        st["matched"],
                        n_els - st["matched"],
                        st["added_cols"],
                        st["manual"] + st["vanished"],
                    )
                )
        except Exception as ex:
            if not forms.alert(
                u"Не удалось прочитать существующий файл для совмещения:\n{}\n\n"
                u"Перезаписать его целиком (доп. столбцы будут потеряны)?".format(ex),
                yes=True, no=True
            ):
                script.exit()

    write_xlsx(path, rows, sheet_name=name, col_widths=col_widths)

    n_cols_final = len(rows[0]) - 1 if rows else n_cols
    forms.alert(
        u"Готово.\n\nСпецификация: {}\nСтрок данных: {}\nСтолбцов: {}{}\n\n{}\n\n"
        u"Правьте значения в Excel (столбец «Revit ID» не трогать) и "
        u"загружайте кнопкой «Импорт из Эксель».".format(
            name, n_els, n_cols_final, merge_line, path
        )
    )

    try:
        os.startfile(path)
    except Exception:
        pass
except Exception:
    forms.alert(
        u"Сбой при экспорте:\n\n{}".format(traceback.format_exc()),
        title=u"Экспорт в Эксель"
    )
