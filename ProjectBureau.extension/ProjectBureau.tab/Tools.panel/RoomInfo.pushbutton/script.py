# -*- coding: utf-8 -*-

__title__ = u"Помещение\nиз связи"
__doc__ = (
    u"Переносит имя и номер помещения из связанной модели в параметр "
    u"выбранных элементов активного документа. Для каждого выбранного "
    u"элемента ищется помещение (Room) во всех подключённых связях, в "
    u"которое попадает точка/центр элемента, и результат в формате "
    u"«Имя (Номер)» записывается в целевой параметр.\n\n"
    u"Shift+клик — настройки (параметр-приёмник и параметр помещения в связи)."
)
__author__ = "Pipers"

from pyrevit import revit, forms, script, EXEC_PARAMS

from pbtools import room_info_settings
from pbtools.room_info import apply_room_info

doc = revit.doc
uidoc = revit.uidoc


def _open_settings():
    edited = room_info_settings.get_settings_interactive()
    forms.alert(
        u"Отменено, настройки не изменены." if edited is None
        else u"Настройки сохранены."
    )


try:
    config_mode = bool(EXEC_PARAMS.config_mode)
except Exception:
    config_mode = False

if config_mode:
    _open_settings()
    script.exit()


settings = room_info_settings.get_settings_silent()
room_info_settings.require(settings, ["target_param_name", "room_number_param_name"])

selected_ids = uidoc.Selection.GetElementIds()

if not selected_ids:
    forms.alert(
        u"Сначала выберите элементы в модели, потом запустите кнопку.",
        exitscript=True
    )

elements = [doc.GetElement(eid) for eid in selected_ids]

with revit.Transaction(u"Помещение из связи"):
    results = apply_room_info(
        doc, elements,
        settings["target_param_name"],
        settings["room_number_param_name"]
    )

written = [r for r in results if r[1] == "written"]
not_found = [r for r in results if r[1] == "not_found"]
no_point = [r for r in results if r[1] == "no_point"]
no_param = [r for r in results if r[1] == "no_param"]
write_error = [r for r in results if r[1] == "write_error"]

forms.alert(
    u"Готово.\n\n"
    u"Записано: {}\n"
    u"Помещение не найдено: {}\n"
    u"Нет точки расположения: {}\n"
    u"Нет параметра «{}»: {}\n"
    u"Ошибка записи: {}".format(
        len(written), len(not_found), len(no_point),
        settings["target_param_name"], len(no_param),
        len(write_error)
    )
)
