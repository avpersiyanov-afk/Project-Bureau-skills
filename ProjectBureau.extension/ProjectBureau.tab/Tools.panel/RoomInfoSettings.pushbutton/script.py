# -*- coding: utf-8 -*-

__title__ = "Параметры\nпомещений"
__doc__ = (
    "Настройка имён параметров для кнопки «Помещение из связи»: в какой "
    "параметр элемента писать результат и как называется в связанной "
    "модели параметр номера помещения. Значения сохраняются между "
    "запусками и общие для всех проектов на этом компьютере."
)
__author__ = "Pipers"

from pyrevit import forms

from pbtools import room_info_settings

settings = room_info_settings.get_settings_interactive()

if settings is None:
    forms.alert(u"Отменено, настройки не изменены.", exitscript=True)

forms.alert(u"Настройки сохранены.")
