# -*- coding: utf-8 -*-

__title__ = u"Семейства\nиз каталога"
__doc__ = u"Актуальность семейств выбранных категорий относительно папки-каталога .rfa и обновление отмеченных (перезагрузка с заменой параметров, при различии имён — переименование). Shift+клик — сменить папку каталога."
__author__ = "Pipers"

import traceback

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from pyrevit import revit, forms, script, EXEC_PARAMS

from pbtools import family_catalog as fc

doc = revit.doc
output = script.get_output()


try:
    try:
        config_mode = bool(EXEC_PARAMS.config_mode)
    except Exception:
        config_mode = False

    # Shift+клик (config_mode) или отсутствующий/битый путь — выбрать папку.
    root = fc.resolve_catalog_root(force_pick=config_mode)
    if not root:
        script.exit()

    entries = fc.scan_catalog(root)
    if not entries:
        forms.alert(
            u"В каталоге не найдено ни одного .rfa:\n{}".format(root),
            exitscript=True
        )

    categories = fc.list_family_categories(doc)
    if not categories:
        forms.alert(u"В проекте нет загружаемых семейств.", exitscript=True)

    chosen = forms.SelectFromList.show(
        categories,
        title=u"Категории семейств (можно несколько)",
        button_name=u"Далее",
        multiselect=True
    )
    if not chosen:
        script.exit()

    cat_ids = [c.cat_id for c in chosen]
    families = fc.list_families_in_categories(doc, cat_ids)
    if not families:
        forms.alert(u"В выбранных категориях нет загружаемых семейств.", exitscript=True)

    rows = fc.build_matches(families, entries)

    picked = fc.show_status_form(rows, root, entries)
    if not picked:
        script.exit()
    jobs, do_rename, overwrite_params = picked

    result = fc.apply_updates(doc, jobs, do_rename, overwrite_params)
    fc.render_result_md(output, result)

    summary = [u"Готово.", u""] + fc.result_summary_lines(result)
    summary += [
        u"",
        u"Подробности — в окне вывода. Каждая перезагрузка — отдельный шаг отмены (Ctrl+Z).",
    ]
    forms.alert(u"\n".join(summary), title=u"Семейства из каталога")

except Exception:
    forms.alert(
        u"Сбой при обновлении семейств:\n\n{}".format(traceback.format_exc()),
        title=u"Семейства из каталога"
    )
