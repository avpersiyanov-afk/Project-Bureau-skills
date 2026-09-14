# -*- coding: utf-8 -*-

__title__ = u"Марки\nпомещений"
__doc__ = (
    u"Расставляет и обновляет марки помещений из связанной модели на "
    u"активном плане. Типоразмер марки выбирается из списка (все "
    u"загруженные марки категории «Марки помещений»). Марки, чья голова "
    u"вышла за границы своего помещения (частая причина «?», Revit сам "
    u"её не пересчитывает), возвращает на место; по-настоящему битые "
    u"марки (АР удалил/переделал помещение, марка осиротела или "
    u"помещение не окружено) пересоздаёт на месте, а если помещения под "
    u"маркой уже нет — удаляет. На время вставки временно снимается "
    u"подложка вида — она мешает Revit ставить марки помещений.\n\n"
    u"Shift+клик — выбрать несколько планов списком, обновить марки "
    u"сразу на всех."
)
__author__ = "Pipers"

import traceback

from Autodesk.Revit.DB import ViewPlan
from pyrevit import revit, forms, EXEC_PARAMS

from pbtools import room_tags

doc = revit.doc


class _Named(object):
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return self.name


class TagTypeOption(_Named):
    def __init__(self, symbol):
        _Named.__init__(self, room_tags.tag_type_label(symbol))
        self.symbol = symbol


class ViewOption(_Named):
    def __init__(self, view):
        _Named.__init__(self, room_tags.view_label(view))
        self.view = view


try:
    pick_views = bool(EXEC_PARAMS.config_mode)
except Exception:
    pick_views = False


if pick_views:
    plan_views = room_tags.get_taggable_plan_views(doc)
    if not plan_views:
        forms.alert(u"В проекте нет планов для простановки марок.", exitscript=True)

    picked = forms.SelectFromList.show(
        sorted([ViewOption(v) for v in plan_views], key=lambda o: o.name.lower()),
        title=u"Виды для обновления марок помещений (можно несколько)",
        button_name=u"Обновить и расставить",
        multiselect=True
    )
    if not picked:
        forms.alert(u"Отменено.", exitscript=True)
    target_views = [o.view for o in picked]
else:
    view = doc.ActiveView
    if view is None or not isinstance(view, ViewPlan):
        forms.alert(
            u"Откройте план этажа или потолочный план — либо нажмите "
            u"кнопку с Shift, чтобы выбрать несколько планов списком.",
            exitscript=True
        )
    if view.IsTemplate:
        forms.alert(u"Активен шаблон вида. Откройте настоящий план.", exitscript=True)
    target_views = [view]


tag_types = room_tags.get_room_tag_types(doc)
if not tag_types:
    forms.alert(
        u"В проекте нет ни одного типоразмера марки помещения. "
        u"Загрузите семейство марки помещения и повторите.",
        exitscript=True
    )

chosen = forms.SelectFromList.show(
    sorted([TagTypeOption(s) for s in tag_types], key=lambda o: o.name.lower()),
    title=u"Марка помещения",
    button_name=u"Обновить и расставить",
    multiselect=False
)
if not chosen:
    forms.alert(u"Отменено.", exitscript=True)


errors = 0
try:
    with revit.Transaction(u"Марки помещений из связи"):
        for target in target_views:
            stats = room_tags.run(doc, target, chosen.symbol.Id)
            errors += stats.get("errors", 0)
except Exception:
    forms.alert(
        u"Сбой при обновлении марок помещений (транзакция отменена, "
        u"изменения не сохранены):\n\n{}".format(traceback.format_exc()),
        title=u"Марки помещений"
    )
else:
    if errors:
        forms.alert(
            u"Готово, но на {} марк(ах)/помещени(ях) обработка сорвалась "
            u"с ошибкой — они пропущены, остальные марки обновлены. Если "
            u"после этого какие-то марки всё ещё показывают «?», "
            u"сообщите — нужно разбираться отдельно.".format(errors)
        )
