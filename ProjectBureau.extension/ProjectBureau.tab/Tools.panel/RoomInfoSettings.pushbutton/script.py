# -*- coding: utf-8 -*-

__title__ = u"Параметры\nпомещений"
__doc__ = (
    u"Настройка имён параметров для кнопки «Помещение из связи»: в какой "
    u"параметр элемента писать результат и как называется в связанной "
    u"модели параметр номера помещения. Значения сохраняются между "
    u"запусками и общие для всех проектов на этом компьютере."
)
__author__ = "Pipers"

from pyrevit import forms

from pbtools import room_info_settings

settings = room_info_settings.get_settings_interactive()

if settings is None:
    forms.alert(u"Отменено, настройки не изменены.", exitscript=True)

forms.alert(u"Настройки сохранены.")
