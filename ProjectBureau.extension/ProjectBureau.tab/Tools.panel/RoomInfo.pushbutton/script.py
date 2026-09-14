# -*- coding: utf-8 -*-

__title__ = u"Запись номера\nпомещения"
__doc__ = (
    u"По нажатию просит выбрать элементы в модели (рамкой и/или кликами — "
    u"связи, аннотация, оси/уровни, обобщённые модели в рамку не "
    u"попадают). Для каждого ищется помещение (Room) во всех подключённых "
    u"связях, в которое попадает точка/центр элемента, и по МАСКЕ "
    u"собирается строка из параметров этого помещения — она пишется в "
    u"целевой параметр.\n\n"
    u"Shift+клик — меню: «Настройки» (параметр-приёмник и маска) либо "
    u"«Прогон по видам» (выбрать виды из списка и назначить помещение "
    u"всем элементам на них)."
)
__author__ = "Pipers"

from pyrevit import revit, forms, script, EXEC_PARAMS

from pbtools import room_info_settings
from pbtools.room_info import apply_room_info
from pbtools.selection import (
    pick_model_elements, collect_model_elements,
    parse_name_prefixes, views_with_name_prefix,
)

doc = revit.doc
uidoc = revit.uidoc


def report(results, target_param_name, head=u""):
    written = [r for r in results if r[1] == "written"]
    not_found = [r for r in results if r[1] == "not_found"]
    no_point = [r for r in results if r[1] == "no_point"]
    no_param = [r for r in results if r[1] == "no_param"]
    write_error = [r for r in results if r[1] == "write_error"]

    forms.alert(
        head +
        u"Готово.\n\n"
        u"Записано: {}\n"
        u"Помещение не найдено: {}\n"
        u"Нет точки расположения: {}\n"
        u"Нет параметра «{}»: {}\n"
        u"Ошибка записи: {}".format(
            len(written), len(not_found), len(no_point),
            target_param_name, len(no_param), len(write_error)
        )
    )


def open_settings():
    edited = room_info_settings.get_settings_interactive()
    forms.alert(
        u"Отменено, настройки не изменены." if edited is None
        else u"Настройки сохранены."
    )


class ViewOption(object):
    def __init__(self, view):
        self.view = view
        self.name = view.Name

    def __str__(self):
        return self.name


def run_by_views(settings):
    room_info_settings.require(settings, ["target_param_name", "room_mask"])

    prefixes = parse_name_prefixes(settings.get("view_name_prefixes"))
    views = views_with_name_prefix(doc, prefixes)
    if not views:
        forms.alert(
            u"Не найдено видов с именем, начинающимся на: {}\n\n"
            u"Поправьте префиксы в настройках (Shift+клик → «Настройки»).".format(
                u", ".join(prefixes) if prefixes else u"(пусто — показываются все)"
            ),
            exitscript=True
        )

    picked = forms.SelectFromList.show(
        [ViewOption(v) for v in views],
        title=u"Виды: назначить помещение всем элементам на них",
        button_name=u"Назначить",
        multiselect=True
    )
    if not picked:
        forms.alert(u"Отменено.", exitscript=True)

    by_id = {}
    for opt in picked:
        for el in collect_model_elements(doc, opt.view):
            by_id[el.Id.IntegerValue] = el
    elements = list(by_id.values())

    if not elements:
        forms.alert(u"На выбранных видах нет подходящих элементов.", exitscript=True)

    with revit.Transaction(u"Запись номера помещения (по видам)"):
        results = apply_room_info(
            doc, elements, settings["target_param_name"], settings["room_mask"]
        )

    report(
        results, settings["target_param_name"],
        head=u"Видов: {}. Элементов обработано: {}\n\n".format(len(picked), len(elements))
    )


try:
    config_mode = bool(EXEC_PARAMS.config_mode)
except Exception:
    config_mode = False

if config_mode:
    choice = forms.alert(
        u"Запись номера помещения — Shift+клик",
        options=[u"Настройки", u"Прогон по видам"]
    )
    if choice == u"Настройки":
        open_settings()
    elif choice == u"Прогон по видам":
        run_by_views(room_info_settings.get_settings_silent())
    script.exit()


settings = room_info_settings.get_settings_silent()
room_info_settings.require(settings, ["target_param_name", "room_mask"])

elements = pick_model_elements(
    uidoc, doc,
    prompt=u"Выберите элементы для простановки помещения "
           u"(рамкой и/или кликами), Enter — готово",
    cancel_message=u"Выбор отменён, ничего не записано.",
    empty_message=u"Не выбрано ни одного элемента."
)

with revit.Transaction(u"Запись номера помещения"):
    results = apply_room_info(
        doc, elements,
        settings["target_param_name"],
        settings["room_mask"]
    )

report(results, settings["target_param_name"])
