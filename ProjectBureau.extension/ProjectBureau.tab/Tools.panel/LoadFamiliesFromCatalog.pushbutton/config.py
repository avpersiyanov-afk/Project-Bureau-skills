# -*- coding: utf-8 -*-
"""Shift+клик по кнопке «Загрузить семейства» — сменить папку-каталог семейств."""

from pyrevit import forms

from pbtools import family_catalog as fc

old = fc.load_catalog_root()
new = fc.pick_catalog_root(old)

if new and new != old:
    msg = u"Новая папка-каталог семейств:\n{}".format(new)
elif new:
    msg = u"Папка-каталог не изменена:\n{}".format(new)
else:
    msg = u"Папка-каталог не задана."

forms.alert(msg, title=u"Загрузить семейства — каталог")
