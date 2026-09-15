# -*- coding: utf-8 -*-

__title__ = u"ID\nэлемента"
__doc__ = (
    u"Показывает и копирует в буфер обмена ID выбранного элемента — в том "
    u"числе элемента, выбранного внутри связанной модели (для него "
    u"показывается ID именно в связанном файле, а не ID экземпляра связи). "
    u"При нескольких выбранных элементах список ID копируется через запятую "
    u"— его можно вставить в диалог Revit «Select Elements by ID»."
)
__author__ = "Pipers"

import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import ElementId, RevitLinkInstance

from pyrevit import revit, forms, script

doc = revit.doc
uidoc = revit.uidoc


def describe_element(el):
    if el is None:
        return None
    try:
        cat = el.Category.Name if el.Category is not None else None
    except:
        cat = None
    try:
        name = el.Name
    except:
        name = None
    parts = [p for p in (cat, name) if p]
    if parts:
        return u" — ".join(parts)
    try:
        return el.GetType().Name
    except:
        return None


def link_display_name(link_inst, linked_doc):
    if linked_doc is not None:
        try:
            title = linked_doc.Title
            if title:
                return title
        except:
            pass
    try:
        return link_inst.Name
    except:
        return u"связанный файл"


def resolve_reference(ref):
    """
    По Reference из uidoc.Selection.GetReferences() определяет, какой именно
    элемент имел в виду пользователь, и его ID для показа.

    Для элемента, выбранного внутри связи, ref.ElementId — это ID самого
    экземпляра связи (RevitLinkInstance) в текущем документе, а нужный нам
    ID элемента — ref.LinkedElementId (ID внутри связанного файла). Именно
    так это выглядит в статус-баре и панели «Свойства» Revit при выборе
    элемента сквозь связь.
    """
    linked_id = ref.LinkedElementId
    if linked_id is not None and linked_id != ElementId.InvalidElementId:
        link_inst = doc.GetElement(ref.ElementId)
        linked_doc = None
        if isinstance(link_inst, RevitLinkInstance):
            try:
                linked_doc = link_inst.GetLinkDocument()
            except:
                linked_doc = None
        el = linked_doc.GetElement(linked_id) if linked_doc is not None else None
        return {
            "id": linked_id.IntegerValue,
            "element": el,
            "link_name": link_display_name(link_inst, linked_doc),
        }

    el = doc.GetElement(ref.ElementId)
    return {
        "id": ref.ElementId.IntegerValue,
        "element": el,
        "link_name": None,
    }


# ------------------------------------------------------------
# ОСНОВНОЙ СЦЕНАРИЙ
# ------------------------------------------------------------

try:
    refs = list(uidoc.Selection.GetReferences())
except:
    refs = []

if not refs:
    forms.alert(
        u"Ничего не выбрано. Выделите элемент (в том числе внутри "
        u"связанного файла) и запустите кнопку снова.",
        title=u"ID элемента",
        exitscript=True
    )

entries = [resolve_reference(r) for r in refs]

if len(entries) == 1:
    e = entries[0]
    desc = describe_element(e["element"])
    lines = [u"ID: {}".format(e["id"])]
    if desc:
        lines.append(u"Элемент: {}".format(desc))
    if e["link_name"]:
        lines.append(u"Связь: {}".format(e["link_name"]))
    lines.append(u"")
    lines.append(u"ID скопирован в буфер обмена.")
    script.clipboard_copy(unicode(e["id"]))
    forms.alert(u"\n".join(lines), title=u"ID элемента")
else:
    lines = []
    for e in entries:
        desc = describe_element(e["element"])
        line = u"{}".format(e["id"])
        extra = u" — ".join([p for p in (desc, e["link_name"]) if p])
        if extra:
            line += u" ({})".format(extra)
        lines.append(line)
    lines.append(u"")
    lines.append(u"Все ID скопированы в буфер обмена через запятую.")
    ids_csv = u", ".join(unicode(e["id"]) for e in entries)
    script.clipboard_copy(ids_csv)
    forms.alert(u"\n".join(lines), title=u"ID элементов ({})".format(len(entries)))
