# -*- coding: utf-8 -*-
"""Общие хелперы чтения/записи параметров элементов Revit."""

from Autodesk.Revit.DB import StorageType, ElementId


def set_element_id_param(el, name, element_id):
    """
    Записывает ElementId в параметр типа StorageType.ElementId (например
    «Проводник» электрической цепи — ссылка на WireType, выбирается в
    Revit выпадающим списком, а не текстом/SetValueString). Возвращает
    True, если запись удалась.
    """
    try:
        if el is None or element_id is None:
            return False

        p = el.LookupParameter(name)
        if not p or p.IsReadOnly or p.StorageType != StorageType.ElementId:
            return False

        p.Set(element_id)
        return True
    except:
        return False


def get_double_param(el, names):
    """Первое числовое значение параметра из списка возможных имён."""
    for name in names:
        try:
            p = el.LookupParameter(name)
            if p and p.HasValue and p.StorageType == StorageType.Double:
                return p.AsDouble()
        except:
            pass
    return None


def set_double_param(el, names, value):
    for name in names:
        try:
            p = el.LookupParameter(name)
            if p and not p.IsReadOnly and p.StorageType == StorageType.Double:
                p.Set(value)
        except:
            pass


def set_string_param(el, name, value):
    try:
        p = el.LookupParameter(name)
        if p and not p.IsReadOnly and p.StorageType == StorageType.String:
            p.Set(u"{}".format(value if value is not None else ""))
    except:
        pass


def get_string_param(el, name):
    """Строковое значение параметра: AsString для текстовых, иначе AsValueString."""
    try:
        p = el.LookupParameter(name)
        if not p or not p.HasValue:
            return None
        if p.StorageType == StorageType.String:
            return p.AsString()
        return p.AsValueString()
    except:
        return None


def get_type_string_param(doc, el, name):
    """
    Строковое значение параметра типа элемента (Type Parameter — например
    «Обозначение», общее у всех экземпляров одного типа семейства).
    el.LookupParameter ищет только среди параметров экземпляра и не видит
    параметры типа, поэтому здесь явно берём тип через GetTypeId().
    """
    try:
        type_el = doc.GetElement(el.GetTypeId())
    except:
        return None
    if type_el is None:
        return None
    return get_string_param(type_el, name)


def get_param_any(el, name):
    """
    Строковое представление значения параметра любого типа хранения
    (String/Integer/Double/ElementId). Для чисел без текстового
    представления (AsValueString) возвращает строку от AsInteger/AsDouble.
    """
    try:
        if el is None:
            return None

        p = el.LookupParameter(name)
        if not p or not p.HasValue:
            return None

        if p.StorageType == StorageType.String:
            v = p.AsString()
            return v.strip() if v else None

        if p.StorageType == StorageType.Integer:
            v = p.AsValueString()
            if v:
                return v.strip()
            try:
                return str(p.AsInteger())
            except:
                return None

        if p.StorageType == StorageType.Double:
            v = p.AsValueString()
            if v:
                return v.strip()
            try:
                return str(p.AsDouble())
            except:
                return None

        if p.StorageType == StorageType.ElementId:
            v = p.AsValueString()
            return v.strip() if v else None

        return None
    except:
        return None


def set_param_any(el, name, value):
    """
    Записывает value в параметр любого типа хранения, подбирая подходящий
    способ (Set(str)/Set(int)/Set(float)/SetValueString). Возвращает True,
    если запись удалась.
    """
    try:
        if el is None:
            return False

        p = el.LookupParameter(name)
        if not p or p.IsReadOnly:
            return False

        if value is None:
            value = ""

        try:
            if p.StorageType == StorageType.String:
                p.Set(str(value))
                return True

            if p.StorageType == StorageType.Integer:
                try:
                    p.Set(int(value))
                    return True
                except:
                    p.SetValueString(str(value))
                    return True

            if p.StorageType == StorageType.Double:
                try:
                    p.Set(float(value))
                    return True
                except:
                    p.SetValueString(str(value))
                    return True

            p.SetValueString(str(value))
            return True
        except:
            try:
                p.Set(str(value))
                return True
            except:
                try:
                    p.SetValueString(str(value))
                    return True
                except:
                    return False
    except:
        return False
