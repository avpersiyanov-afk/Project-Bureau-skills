# -*- coding: utf-8 -*-

__title__ = u"Марки\nпомещений"
__doc__ = (
    u"Расставляет и обновляет марки помещений из связанной модели на "
    u"активном плане. Типоразмер марки выбирается из списка (все "
    u"загруженные марки категории «Марки помещений»). Марки, ставшие "
    u"«???» после того как АР переделал помещение, пересоздаются на "
    u"месте. На время вставки временно снимается подложка вида — она "
    u"мешает Revit ставить марки помещений."
)
__author__ = "Pipers"

from Autodesk.Revit.DB import ViewPlan
from pyrevit import revit, forms

from pbtools import room_tags

doc = revit.doc
view = doc.ActiveView

if view is None or not isinstance(view, ViewPlan):
    forms.alert(
        u"Откройте план этажа или потолочный план — марки помещений "
        u"ставятся на планах.",
        exitscript=True
    )

if view.IsTemplate:
    forms.alert(u"Активен шаблон вида. Откройте настоящий план.", exitscript=True)

tag_types = room_tags.get_room_tag_types(doc)
if not tag_types:
    forms.alert(
        u"В проекте нет ни одного типоразмера марки помещения. "
        u"Загрузите семейство марки помещения и повторите.",
        exitscript=True
    )


class TagTypeOption(object):
    def __init__(self, symbol):
        self.symbol = symbol
        self.name = room_tags.tag_type_label(symbol)

    def __str__(self):
        return self.name


options = sorted(
    [TagTypeOption(s) for s in tag_types],
    key=lambda o: o.name.lower()
)

chosen = forms.SelectFromList.show(
    options,
    title=u"Марка помещения",
    button_name=u"Обновить и расставить",
    multiselect=False
)

if not chosen:
    forms.alert(u"Отменено.", exitscript=True)

with revit.Transaction(u"Марки помещений из связи"):
    stats = room_tags.run(doc, view, chosen.symbol.Id)

forms.alert(
    u"Готово.\n\n"
    u"Добавлено марок: {added}\n"
    u"Пересоздано «???»: {recreated}\n"
    u"Сменён типоразмер: {retyped}\n"
    u"Уже стояли: {already}\n"
    u"«???» без помещения рядом: {orphan_unresolved}\n"
    u"Помещений вне области вида: {out_of_view}\n"
    u"Помещений без точки размещения: {room_no_point}".format(**stats)
)
