# -*- coding: utf-8 -*-

__title__ = "Длина\nлиний"
__doc__ = "Сумма длин выбранных обобщённых моделей по типам"
__author__ = "Pipers"

from pyrevit import revit, DB, script as pyrevit_script

doc = revit.doc
selection = revit.get_selection()
output = pyrevit_script.get_output()

generic_cat_id = DB.ElementId(DB.BuiltInCategory.OST_GenericModel)


def get_type_name(el):
    type_id = el.GetTypeId()

    if type_id and type_id != DB.ElementId.InvalidElementId:
        type_el = doc.GetElement(type_id)

        if type_el:
            p = type_el.get_Parameter(DB.BuiltInParameter.SYMBOL_NAME_PARAM)
            if p and p.HasValue:
                return p.AsString()

            try:
                return type_el.Name
            except:
                return "Тип ID {}".format(type_el.Id.IntegerValue)

    return "Без типа"


def get_length_ft(el):
    """
    Возвращает длину в футах.
    """

    loc = el.Location

    if isinstance(loc, DB.LocationCurve):
        return loc.Curve.Length

    # Параметры экземпляра
    for param_name in ["Длина", "Length", "L", "длина"]:
        p = el.LookupParameter(param_name)
        if p and p.HasValue and p.StorageType == DB.StorageType.Double:
            return p.AsDouble()

    # Параметры типа
    type_id = el.GetTypeId()
    if type_id and type_id != DB.ElementId.InvalidElementId:
        type_el = doc.GetElement(type_id)

        if type_el:
            for param_name in ["Длина", "Length", "L", "длина"]:
                p = type_el.LookupParameter(param_name)
                if p and p.HasValue and p.StorageType == DB.StorageType.Double:
                    return p.AsDouble()

    return 0.0


try:
    selected = list(selection.elements)
except:
    selected = list(selection)


if not selected:
    output.print_md(u"Сначала выберите обобщённые модели.")
else:
    totals = {}

    for el in selected:
        if not el.Category:
            continue

        if el.Category.Id.IntegerValue != generic_cat_id.IntegerValue:
            continue

        type_name = get_type_name(el)
        length_ft = get_length_ft(el)

        if type_name not in totals:
            totals[type_name] = 0.0

        totals[type_name] += length_ft

    if not totals:
        output.print_md(u"Среди выбранных элементов нет обобщённых моделей.")
    else:
        total_ft = 0.0

        output.print_md(u"### Длина по типам")

        for type_name in sorted(totals.keys()):
            length_ft = totals[type_name]
            total_ft += length_ft

            length_m = length_ft * 0.3048
            output.print_md(u"- {} — {:.2f} м".format(type_name, length_m))

        output.print_md(u"**Общая длина — {:.2f} м**".format(total_ft * 0.3048))
