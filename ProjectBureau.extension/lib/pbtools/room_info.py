# -*- coding: utf-8 -*-
"""
Перенос имени и номера помещения из связанной модели в параметр элемента
активного документа.

Для каждого элемента ищется точка (точка вставки, середина кривой для
line-based элементов, либо центр bounding box), затем среди ВСЕХ
подключённых связей ищется Room, в который попадает эта точка, и в целевой
параметр элемента записывается "Имя (Номер)" (или только то, что удалось
найти).

Поиск помещения двухпроходный:
  1. точное попадание внутрь Room (Room.IsPointInRoom) — как было;
  2. если точное попадание не нашлось — ближайший Room, чей контур
     отстоит от точки не дальше ROOM_TOLERANCE_MM по горизонтали.
Второй проход нужен, потому что оборудование часто ставят с заглублением
в стену (стены обычно ~200 мм), и точка семейства оказывается за контуром
Room на 1-2 см, из-за чего IsPointInRoom возвращает False.

Имена параметров (куда писать результат, из какого параметра связанного
Room брать номер) — соглашения конкретного проекта, поэтому не зашиты
здесь, а приходят из room_info_settings.py.
"""

from Autodesk.Revit.DB import (
    RevitLinkInstance, FilteredElementCollector, BuiltInCategory,
    BuiltInParameter, LocationCurve, SpatialElementBoundaryOptions, XYZ
)

from pbtools.geometry import get_point as get_location_point
from pbtools.params import set_param_any, get_string_param


# Насколько далеко точка может отстоять от контура Room и всё ещё
# считаться принадлежащей этому помещению. Перекрывает примерно полстены
# при толщине 200 мм, но недостаточно велик, чтобы «перепрыгнуть» стену
# в соседнее помещение. Менять здесь — в окно настроек не выведено
# намеренно (одна фиксированная величина, не стоит формы).
ROOM_TOLERANCE_MM = 90.0
_ROOM_TOLERANCE_FT = ROOM_TOLERANCE_MM / 304.8


def get_point(el):
    """
    Точка для поиска помещения. Шире, чем geometry.get_point (только
    LocationPoint) — кнопка должна работать с произвольными категориями
    элементов, поэтому дополнительно берёт середину кривой (line-based
    элементы) и центр bounding box как последний резерв.
    """
    p = get_location_point(el)
    if p is not None:
        return p

    try:
        loc = el.Location
        if isinstance(loc, LocationCurve):
            return loc.Curve.Evaluate(0.5, True)
    except:
        pass

    try:
        bbox = el.get_BoundingBox(None)
        if bbox:
            return (bbox.Min + bbox.Max) / 2
    except:
        pass

    return None


def _collect_rooms(linked_doc):
    return FilteredElementCollector(linked_doc) \
        .OfCategory(BuiltInCategory.OST_Rooms) \
        .WhereElementIsNotElementType() \
        .ToElements()


def _room_name_number(room, room_number_param_name):
    """(имя, номер) одного Room — имя из нативного ROOM_NAME, номер из
    общего параметра проекта. Любое может быть None."""
    name_param = room.get_Parameter(BuiltInParameter.ROOM_NAME)
    room_name = name_param.AsString() if name_param and name_param.HasValue else None

    room_number = get_string_param(room, room_number_param_name) if room_number_param_name else None

    return room_name, room_number


def _distance_to_room_boundary(room, point):
    """
    Кратчайшее расстояние по горизонтали от point до контура Room, во
    внутренних единицах Revit (футы). Возвращает None, если:
      - у Room нет bounding box или контура (неразмещённый/незамкнутый);
      - точка по высоте вне объёма помещения (±допуск) — чтобы не цеплять
        помещение этажом выше/ниже, случайно близкое в плане.
    """
    try:
        bbox = room.get_BoundingBox(None)
    except:
        bbox = None
    if bbox is None:
        return None
    if not (bbox.Min.Z - _ROOM_TOLERANCE_FT <= point.Z <= bbox.Max.Z + _ROOM_TOLERANCE_FT):
        return None

    try:
        loops = room.GetBoundarySegments(SpatialElementBoundaryOptions())
    except:
        loops = None
    if not loops:
        return None

    best = None
    for loop in loops:
        for seg in loop:
            try:
                curve = seg.GetCurve()
            except:
                curve = None
            if curve is None:
                continue

            # Контур лежит на отметке помещения — сравниваем в плане,
            # подставляя Z кривой, иначе Curve.Distance учтёт перепад
            # высот между точкой монтажа и полом.
            flat = XYZ(point.X, point.Y, curve.GetEndPoint(0).Z)
            try:
                d = curve.Distance(flat)
            except:
                continue

            if best is None or d < best:
                best = d

    return best


def _find_room(doc, point):
    """
    Ищет и возвращает сам элемент Room (не имя/номер), которому
    принадлежит point, во всех RevitLinkInstance активного документа —
    общий поиск для find_room_info и find_room_param_value. Проход 1 —
    точное попадание внутрь (Room.IsPointInRoom). Проход 2 (если точного
    нет) — ближайший Room, чей контур не дальше ROOM_TOLERANCE_MM от
    точки по горизонтали. None, если точка пуста или ничего не найдено.
    """
    if point is None:
        return None

    link_instances = FilteredElementCollector(doc).OfClass(RevitLinkInstance).ToElements()

    rooms_by_doc = []
    for link in link_instances:
        linked_doc = link.GetLinkDocument()
        if linked_doc is None:
            continue
        rooms_by_doc.append(_collect_rooms(linked_doc))

    # Проход 1: точное попадание внутрь Room.
    for rooms in rooms_by_doc:
        for room in rooms:
            try:
                in_room = room.IsPointInRoom(point)
            except:
                in_room = False
            if in_room:
                return room

    # Проход 2: точка чуть за контуром (сидит в стене) — ближайший Room
    # в пределах допуска.
    best_dist = None
    best_room = None
    for rooms in rooms_by_doc:
        for room in rooms:
            d = _distance_to_room_boundary(room, point)
            if d is None or d > _ROOM_TOLERANCE_FT:
                continue
            if best_dist is None or d < best_dist:
                best_dist = d
                best_room = room

    return best_room


def find_room_info(doc, point, room_number_param_name):
    """
    Ищет Room, которому принадлежит point (см. _find_room), и возвращает
    (имя, номер) — любое из двух может быть None, если Room не найден или
    у него не заполнено.
    """
    room = _find_room(doc, point)
    if room is None:
        return None, None
    return _room_name_number(room, room_number_param_name)


def find_room_param_value(doc, point, param_name):
    """
    Значение произвольного текстового параметра НА САМОМ элементе Room в
    связанной модели (не имя/номер помещения — см. find_room_info, а
    любой другой параметр, заполняемый на помещении как таковом, например
    признак принадлежности к какой-то группе/зоне) — тем же поиском, что
    и find_room_info (точное попадание, иначе ближайший в пределах
    допуска). None, если параметр не задан, Room не найден, либо
    параметра на нём нет/он пуст.
    """
    if not param_name:
        return None

    room = _find_room(doc, point)
    if room is None:
        return None

    value = get_string_param(room, param_name)
    return value.strip() if value and value.strip() else None


def format_room_value(room_name, room_number):
    """"Имя (Номер)" — либо то, что удалось найти по отдельности, либо пустая строка."""
    if room_name and room_number:
        return u"{} ({})".format(room_name, room_number)
    if room_name:
        return room_name
    if room_number:
        return u"({})".format(room_number)
    return u""


def apply_room_info(doc, elements, target_param_name, room_number_param_name):
    """
    Для каждого элемента ищет связанное помещение и пишет результат в
    target_param_name. Возвращает список (element, status, value), где
    status — "written"/"not_found"/"no_point"/"no_param"/"write_error".
    Транзакцию открывает вызывающий скрипт кнопки.
    """
    results = []

    for el in elements:
        if el is None:
            continue

        point = get_point(el)
        if point is None:
            results.append((el, "no_point", u""))
            continue

        room_name, room_number = find_room_info(doc, point, room_number_param_name)
        value = format_room_value(room_name, room_number)

        if not value:
            results.append((el, "not_found", u""))
            continue

        if el.LookupParameter(target_param_name) is None:
            results.append((el, "no_param", value))
            continue

        ok = set_param_any(el, target_param_name, value)
        results.append((el, "written" if ok else "write_error", value))

    return results
