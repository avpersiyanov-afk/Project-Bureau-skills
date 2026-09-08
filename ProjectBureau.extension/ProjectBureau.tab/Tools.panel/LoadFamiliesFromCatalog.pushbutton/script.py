# -*- coding: utf-8 -*-

__title__ = u"Загрузить\nсемейства"
__doc__ = u"Загружает семейства из папки-каталога .rfa в модель: выбор разделов (папок) каталога, таблица с галочками по файлам, затем окно выбора типоразмеров. Shift+клик — сменить папку каталога."
__author__ = "Pipers"

import os
import traceback

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from pyrevit import revit, forms, script, EXEC_PARAMS

from pbtools import family_catalog as fc

doc = revit.doc
output = script.get_output()


class FolderOption(object):
    def __init__(self, rel):
        self.rel = rel
        self.name = rel if rel != u"." else u"(корень каталога)"

    def __str__(self):
        return self.name


try:
    try:
        config_mode = bool(EXEC_PARAMS.config_mode)
    except Exception:
        config_mode = False

    root = fc.resolve_catalog_root(force_pick=config_mode)
    if not root:
        script.exit()

    entries = fc.scan_catalog(root)
    if not entries:
        forms.alert(
            u"В каталоге не найдено ни одного .rfa:\n{}".format(root),
            exitscript=True
        )

    # Разделы каталога = папки с .rfa (у не загруженного .rfa категорию
    # Revit не узнать, не открыв файл, поэтому фильтруем по папкам).
    folders = sorted(set(os.path.dirname(e.rel) or u"." for e in entries))
    if len(folders) > 1:
        chosen = forms.SelectFromList.show(
            [FolderOption(f) for f in folders],
            title=u"Разделы каталога — можно несколько (Отмена = все)",
            button_name=u"Далее",
            multiselect=True
        )
        if chosen:
            keep = set(o.rel for o in chosen)
            entries = [e for e in entries if (os.path.dirname(e.rel) or u".") in keep]

    if not entries:
        forms.alert(u"В выбранных разделах нет .rfa.", exitscript=True)

    present = fc.project_family_names(doc)

    # Шаги: окно выбора файлов -> окно выбора типоразмеров (всегда).
    # «← Назад» в окне типоразмеров возвращает к окну выбора файлов.
    jobs = None
    while jobs is None:
        picked = fc.show_load_form(entries, present, root)
        if not picked:
            script.exit()
        load_entries, overwrite_params = picked

        app = doc.Application
        # type_map: (entry, [имена типоразмеров]); passthru: семейства,
        # у которых типы не прочитались — грузим целиком.
        type_map, passthru = fc.read_family_types_batch(app, load_entries)

        sel = fc.show_type_picker(type_map)
        if sel is None:
            script.exit()
        if sel == fc.BACK:
            continue  # назад к окну выбора файлов

        jobs = [(e, None) for e in passthru]
        for e, names in type_map:
            chosen = sel.get(e)
            if not chosen:
                continue
            jobs.append((e, None if set(chosen) == set(names) else sorted(chosen)))

    if not jobs:
        forms.alert(u"Нечего загружать.", exitscript=True)

    result = fc.apply_loads(doc, jobs, present, overwrite_params)
    fc.render_load_result_md(output, result)

    summary = [u"Готово.", u""] + fc.load_summary_lines(result)
    summary += [
        u"",
        u"Подробности — в окне вывода. Каждая загрузка — отдельный шаг отмены (Ctrl+Z).",
    ]
    forms.alert(u"\n".join(summary), title=u"Загрузить семейства")

except Exception:
    forms.alert(
        u"Сбой при загрузке семейств:\n\n{}".format(traceback.format_exc()),
        title=u"Загрузить семейства"
    )
