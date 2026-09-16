# -*- coding: utf-8 -*-
"""Общие хелперы работы с выделением элементов в Revit UI."""

from pyrevit import forms

from Autodesk.Revit.DB import (
    BuiltInCategory, CategoryType, FilteredElementCollector, View, ViewType
)
from Autodesk.Revit.UI.Selection import ObjectType, ISelectionFilter
from Autodesk.Revit.Exceptions import OperationCanceledException


def get_single_selection(
    doc,
    uidoc,
    empty_message=u"Сначала выберите элемент в Revit, потом запустите кнопку.",
    multiple_message=u"Выберите только один элемент."
):
    """Возвращает единственный выбранный элемент либо останавливает скрипт."""
    selected_ids = uidoc.Selection.GetElementIds()

    if not selected_ids:
        forms.alert(empty_message, exitscript=True)

    if len(selected_ids) > 1:
        forms.alert(multiple_message, exitscript=True)

    el_id = list(selected_ids)[0]
    return doc.GetElement(el_id)


def _bic_ids(*names):
    """Множество int-id встроенных категорий по именам; несуществующее имя
    в этой версии Revit молча пропускается."""
    ids = set()
    for name in names:
        try:
            ids.add(int(getattr(BuiltInCategory, name)))
        except Exception:
            pass
    return ids


# То, что обычно случайно попадает в рамку выбора, но оборудованием
# модели не является. Аннотацию в целом отсекает проверка
# CategoryType.Annotation ниже; здесь — модельные/служебные категории,
# которые под неё не подпадают.
_JUNK_PICK_CATEGORY_IDS = _bic_ids(
    "OST_Grids", "OST_Levels", "OST_Lines", "OST_CLines", "OST_SketchLines",
    "OST_TextNotes", "OST_GenericAnnotation", "OST_RvtLinks",
    "OST_GenericModel",
    "OST_Rooms", "OST_MEPSpaces", "OST_Areas",
    "OST_SectionBox", "OST_Cameras", "OST_Viewers", "OST_ScopeBoxes",
)


def is_pickable_model_element(elem):
    """
    True для элемента текущей модели, который не является аннотацией или
    служебной геометрией: отсекает элементы связанных файлов, всю
    аннотацию (CategoryType.Annotation — марки, размеры, текст), оси,
    уровни, линии, опорные плоскости, обобщённые модели, вставки связей,
    помещения/зоны/площади, рамки подрезки, виды/камеры.
    """
    try:
        if elem.Document.IsLinked:
            return False
    except Exception:
        pass

    try:
        cat = elem.Category
    except Exception:
        cat = None

    if cat is None:
        return False

    try:
        if cat.Id.IntegerValue in _JUNK_PICK_CATEGORY_IDS:
            return False
    except Exception:
        pass

    try:
        if cat.CategoryType == CategoryType.Annotation:
            return False
    except Exception:
        pass

    return True


class ModelElementSelectionFilter(ISelectionFilter):
    """ISelectionFilter поверх is_pickable_model_element."""

    # __namespace__ обязателен под pythonnet (в отличие от IronPython) —
    # без него конструктор класса, реализующего .NET-интерфейс напрямую,
    # падает с "TypeError: interface takes exactly one argument".
    __namespace__ = "ProjectBureau.Interop"

    def AllowElement(self, elem):
        return is_pickable_model_element(elem)

    def AllowReference(self, reference, position):
        return True


def collect_model_elements(doc, view):
    """
    Все элементы модели, видимые на view (FilteredElementCollector по
    виду), отфильтрованные тем же критерием, что и интерактивный выбор
    (is_pickable_model_element). Возвращает список Element.
    """
    try:
        raw = (FilteredElementCollector(doc, view.Id)
               .WhereElementIsNotElementType()
               .ToElements())
    except Exception:
        return []
    return [el for el in raw if is_pickable_model_element(el)]


def parse_name_prefixes(text):
    """«1, 2, 20, 30, 60» -> ('1', '2', '20', '30', '60'). Разделители —
    запятая, точка с запятой, перевод строки. Пустые куски отброшены."""
    if not text:
        return tuple()
    for sep in (u";", u"\n", u"\r", u"\t"):
        text = text.replace(sep, u",")
    return tuple(part.strip() for part in text.split(u",") if part.strip())


def views_with_name_prefix(doc, prefixes):
    """
    Графические виды (не шаблоны, не листы/легенды/спецификации/браузеры),
    чьё имя начинается с одного из prefixes. Пустой prefixes -> все такие
    виды. Отсортированы по имени.
    """
    prefixes = tuple(prefixes or ())
    skip_types = set()
    for name in ("Schedule", "DrawingSheet", "Legend", "Internal",
                 "ProjectBrowser", "SystemBrowser", "Undefined", "Report",
                 "PanelSchedule", "ColumnSchedule"):
        t = getattr(ViewType, name, None)
        if t is not None:
            skip_types.add(t)

    out = []
    for view in FilteredElementCollector(doc).OfClass(View):
        try:
            if view.IsTemplate:
                continue
            if view.ViewType in skip_types:
                continue
            name = view.Name
        except Exception:
            continue
        if not prefixes or name.startswith(prefixes):
            out.append(view)

    out.sort(key=lambda v: v.Name.lower())
    return out


class CategoryOption(object):
    """Категория — для forms.SelectFromList."""

    def __init__(self, category):
        self.category_id = category.Id
        try:
            base = category.Name
        except Exception:
            base = None
        base = base or u"?"
        self.raw_name = base
        self.sort_name = base.lower()
        self.name = base

    def __str__(self):
        return self.name


def list_view_categories(doc, view):
    """
    Категории «модельных» элементов (is_pickable_model_element), видимых на
    view — отсортированный по имени список CategoryOption для
    forms.SelectFromList(multiselect=True).
    """
    cats = {}
    for el in collect_model_elements(doc, view):
        cat = el.Category
        if cat is None:
            continue
        cats[cat.Id.IntegerValue] = cat

    options = [CategoryOption(cat) for cat in cats.values()]
    options.sort(key=lambda o: o.sort_name)
    return options


def _category_id_of(elem):
    try:
        cat = elem.Category
        return cat.Id.IntegerValue if cat is not None else None
    except Exception:
        return None


def pick_elements_by_categories(
    uidoc,
    doc,
    category_ids,
    prompt=(u"Выделяйте элементы кликом и/или рамкой. Enter (или ПКМ — "
            u"«Готово») — завершить, Esc — отмена."),
    cancel_message=u"Выбор отменён.",
    empty_message=u"Не выбрано ни одного элемента.",
):
    """
    Интерактивный выбор, отфильтрованный по категориям (category_ids —
    набор int Category.Id.IntegerValue), но БЕЗ ISelectionFilter-колбэка
    во время самого PickObjects — фильтрация происходит один раз, после,
    на уже отобранных элементах.

    Раньше здесь был CategorySelectionFilter (ISelectionFilter.AllowElement
    на каждый элемент). Revit вызывает AllowElement для КАЖДОГО кандидата,
    который проверяет при разрешении рамки/клика — не только для итогово
    попавших в выбор, а Python-колбэк на границе managed/IronPython на
    порядок медленнее нативной C#-проверки. На захламлённом виде с сотнями
    элементов под рамкой это ощутимо тормозило именно момент завершения
    выбора (Enter/отпускание рамки) — то, на что жаловался пользователь.
    Без колбэка PickObjects целиком нативный и быстрый; отбор по
    категориям — один проход по уже picked-элементам (их всегда на
    порядки меньше кандидатов под рамкой).
    """
    try:
        refs = uidoc.Selection.PickObjects(ObjectType.Element, prompt)
    except OperationCanceledException:
        forms.alert(cancel_message, exitscript=True)
        return []

    category_ids = set(category_ids)
    els = [doc.GetElement(r) for r in refs]
    els = [el for el in els
           if el is not None and _category_id_of(el) in category_ids]

    if not els:
        forms.alert(empty_message, exitscript=True)

    return els


def pick_model_elements(
    uidoc,
    doc,
    prompt=u"Выберите элементы (рамкой и/или кликами), подтвердите Enter",
    cancel_message=u"Выбор отменён.",
    empty_message=u"Не выбрано ни одного элемента.",
):
    """
    Интерактивный выбор элементов модели: PickObjects с фильтром
    ModelElementSelectionFilter, поэтому рамкой не захватываются элементы
    связей, аннотация, оси/уровни/линии и прочая служебка. Останавливает
    скрипт (forms.alert exitscript) при отмене (Esc) или пустом выборе.
    Возвращает список Element.
    """
    try:
        refs = uidoc.Selection.PickObjects(
            ObjectType.Element, ModelElementSelectionFilter(), prompt
        )
    except OperationCanceledException:
        forms.alert(cancel_message, exitscript=True)
        return []

    els = [doc.GetElement(r) for r in refs]
    els = [el for el in els if el is not None]

    if not els:
        forms.alert(empty_message, exitscript=True)

    return els
