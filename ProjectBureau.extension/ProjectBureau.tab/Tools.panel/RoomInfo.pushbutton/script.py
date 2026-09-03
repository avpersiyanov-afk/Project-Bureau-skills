# -*- coding: utf-8 -*-

__title__ = "Помещение\nиз связи"
__doc__ = (
    "Переносит имя и номер помещения из связанной модели в параметр "
    "выбранных элементов активного документа. Для каждого выбранного "
    "элемента ищется помещение (Room) во всех подключённых связях, в "
    "которое попадает точка/центр элемента, и результат в формате "
    "«Имя (Номер)» записывается в целевой параметр. Перед первым "
    "использованием заполните имена параметров кнопкой «Параметры "
    "помещений»."
)
__author__ = "Pipers"

from pyrevit import revit, forms

from pbtools import room_info_settings
from pbtools.room_info import apply_room_info

doc = revit.doc
uidoc = revit.uidoc

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
