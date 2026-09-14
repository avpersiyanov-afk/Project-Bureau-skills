# -*- coding: utf-8 -*-
"""
Перенос значений помещения из связанной модели в параметр элемента
активного документа.

Для каждого элемента ищется точка (точка вставки, середина кривой для
line-based элементов, либо центр bounding box), затем среди ВСЕХ
подключённых связей ищется Room, в который попадает эта точка, и в целевой
параметр элемента записывается строка, собранная по МАСКЕ.

Маска (render_room_mask): имена параметров помещения (Room) связи,
разделённые запятыми и/или скобками. Прочие символы — как есть.
  «Имя, Номер»    -> «Офис, 212»
  «Имя (Номер)»   -> «Офис (212)»
  «Номер»         -> «212»
«Имя»/«Номер» (без учёта регистра, а также Name/Number) — псевдонимы
нативных ROOM_NAME / ROOM_NUMBER; остальные токены ищутся как параметры
Room по имени. Токен без значения выпадает вместе с осиротевшими
скобками/запятыми.

Поиск помещения двухпроходный:
  1. точное попадание внутрь Room (Room.IsPointInRoom) — как было;
  2. если точное попадание не нашлось — ближайший Room, чей контур
     отстоит от точки не дальше ROOM_TOLERANCE_MM по горизонтали.
Второй проход нужен, потому что оборудование часто ставят с заглублением
в стену (стены обычно ~200 мм), и точка семейства оказывается за контуром
Room на 1-2 см, из-за чего IsPointInRoom возвращает False.

Куда писать результат и сама маска — соглашения конкретного проекта,
поэтому не зашиты здесь, а приходят из настроек (room_info_settings.py).
"""

import re

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


_ROOM_NAME_ALIASES = (u"имя", u"name", u"имя помещения", u"room name")
_ROOM_NUMBER_ALIASES = (u"номер", u"number", u"номер помещения", u"room number")

# Разделители маски: запятая и круглые скобки. По ним режем маску на
# токены-имена параметров, сами разделители сохраняем как есть.
_MASK_SPLIT_RE = re.compile(u"([(),])")


def _room_param_value(room, name):
    """
    Строковое значение параметра помещения Room по имени токена маски.
    «Имя»/«Номер» (и англ. Name/Number) — нативные ROOM_NAME/ROOM_NUMBER,
    остальное ищется как обычный параметр Room. Пустая строка, если не
    нашлось/пусто.
    """
    key = (name or u"").strip().lower()

    if key in _ROOM_NAME_ALIASES:
        p = room.get_Parameter(BuiltInParameter.ROOM_NAME)
        if p is not None and p.HasValue:
            return (p.AsString() or u"").strip()

    if key in _ROOM_NUMBER_ALIASES:
        p = room.get_Parameter(BuiltInParameter.ROOM_NUMBER)
        if p is not None and p.HasValue:
            return (p.AsString() or u"").strip()

    value = get_string_param(room, name)
    if value and value.strip():
        return value.strip()

    try:
        p = room.LookupParameter(name)
        if p is not None and p.HasValue:
            for getter in (p.AsString, p.AsValueString):
                try:
                    s = getter()
                except Exception:
                    s = None
                if s and s.strip():
                    return s.strip()
    except Exception:
        pass

    return u""


def _cleanup_mask_result(text):
    """Убрать следы выпавших токенов: пустые скобки, сдвоенные и
    висящие по краям запятые, лишние пробелы у скобок."""
    text = re.sub(u"\\(\\s*\\)", u"", text)
    text = re.sub(u"\\(\\s+", u"(", text)
    text = re.sub(u"\\s+\\)", u")", text)
    text = re.sub(u"\\s+,", u",", text)
    text = re.sub(u"(,\\s*){2,}", u", ", text)
    text = re.sub(u"[ \\t]{2,}", u" ", text)
    return text.strip().strip(u",").strip()


def render_room_mask(room, mask):
    """
    Строка по маске для одного Room. Имена параметров в маске заменяются
    их значениями, разделители (, ( ) ) и прочий текст — как есть. Токен
    без значения выпадает вместе с прилегающими осиротевшими скобками и
    запятыми. Пустая маска или отсутствие значений -> "".
    """
    if not mask or not mask.strip():
        return u""

    out = []
    for chunk in _MASK_SPLIT_RE.split(mask):
        if chunk in (u"(", u")", u","):
            out.append(chunk)
            continue

        core = chunk.strip()
        if not core:
            out.append(chunk)
            continue

        value = _room_param_value(room, core)
        if not value:
            continue

        lead = chunk[:len(chunk) - len(chunk.lstrip())]
        trail = chunk[len(chunk.rstrip()):]
        out.append(lead + value + trail)

    return _cleanup_mask_result(u"".join(out))


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
    общий поиск для find_room_value и find_room_param_value. Проход 1 —
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


def find_room_value(doc, point, mask):
    """
    Ищет Room под точкой (см. _find_room) и собирает по нему строку по
    маске (render_room_mask). "" — если Room не найден или маска ничего
    не дала. Заменяет прежнюю пару find_room_info + format_room_value.
    """
    room = _find_room(doc, point)
    if room is None:
        return u""
    return render_room_mask(room, mask)


def find_room_param_value(doc, point, param_name):
    """
    Значение произвольного текстового параметра НА САМОМ элементе Room в
    связанной модели (не собранное по маске значение — см. find_room_value,
    а один конкретный параметр, например признак принадлежности к
    какой-то группе/зоне) — тем же поиском (точное попадание, иначе
    ближайший в пределах допуска). None, если параметр не задан, Room не
    найден, либо параметра на нём нет/он пуст.
    """
    if not param_name:
        return None

    room = _find_room(doc, point)
    if room is None:
        return None

    value = get_string_param(room, param_name)
    return value.strip() if value and value.strip() else None


def apply_room_info(doc, elements, target_param_name, mask):
    """
    Для каждого элемента ищет связанное помещение и пишет в
    target_param_name строку, собранную по маске. Возвращает список
    (element, status, value), где status —
    "written"/"not_found"/"no_point"/"no_param"/"write_error".
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

        value = find_room_value(doc, point, mask)

        if not value:
            results.append((el, "not_found", u""))
            continue

        if el.LookupParameter(target_param_name) is None:
            results.append((el, "no_param", value))
            continue

        ok = set_param_any(el, target_param_name, value)
        results.append((el, "written" if ok else "write_error", value))

    return results
