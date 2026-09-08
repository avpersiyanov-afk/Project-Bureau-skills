# -*- coding: utf-8 -*-
"""
Расстановка и обновление марок помещений (Room Tag) на активном плане,
когда сами помещения живут в связанной модели (АР), а марки нужны в
текущем документе.

Что делает :func:`run` за один проход по активному виду:

  1. **Пересоздаёт «мёртвые» марки.** Если АР удалил/пересоздал помещение
     (даже с тем же номером), старая марка в нашем документе перестаёт
     находить своё помещение и показывает «???». Такие марки нельзя
     «перепривязать» через API — поэтому под каждой из них ищется
     помещение связи по координате головы марки, старая марка удаляется,
     на её месте создаётся новая (голова и наличие полки сохраняются).
  2. **Меняет типоразмер** всех живых марок помещений на виде на
     выбранный пользователем (единый вид марок на листе).
  3. **Добавляет недостающие марки** для тех помещений связи, что попадают
     на уровень вида и в его область подрезки, но ещё не помечены.

Обход грабли с подложкой: при включённой подложке Revit отказывается
ставить марку помещения («point is not in a room» / марка садится на
помещение с подложки). Поэтому на время операции подложка вида
отключается и в конце возвращается (см. :func:`_suppress_underlay`).

Марки для помещений из связи создаются через
``doc.Create.NewRoomTag(LinkElementId(link.Id, room.Id), UV, viewId)`` —
единственный путь в API; аналога ``IndependentTag.Create`` для помещений
нет. Транзакцию открывает вызывающий скрипт кнопки.
"""

from Autodesk.Revit.DB import (
    FilteredElementCollector, BuiltInCategory, RevitLinkInstance,
    LinkElementId, ElementId, UV, LocationPoint, Element, Family,
)

MM_IN_FOOT = 304.8

# Насколько отметка точки помещения (в координатах хоста) может
# отличаться от отметки уровня вида и всё ещё считаться «на этом плане».
# Меньше типового межэтажного расстояния, чтобы не поймать этаж выше.
_LEVEL_TOL_MM = 1500.0
_LEVEL_TOL_FT = _LEVEL_TOL_MM / MM_IN_FOOT

# Допуск для «голова мёртвой марки почти внутри помещения» — помещение
# чуть сдвинули/перерисовали, точная проверка IsPointInRoom не сработала.
_NEAR_TOL_MM = 400.0
_NEAR_TOL_FT = _NEAR_TOL_MM / MM_IN_FOOT

_OST_ROOM_TAGS = int(BuiltInCategory.OST_RoomTags)


# ---------------------------------------------------------------------------
# Типоразмеры марок помещений
# ---------------------------------------------------------------------------

def get_room_tag_types(doc):
    """
    Все загруженные типоразмеры марок помещений, включая ещё не
    вставленные. Идём через Family -> GetFamilySymbolIds (см. грабли в
    CLAUDE.md: FilteredElementCollector(...).OfClass(FamilySymbol) может
    пропустить невставленные типы).
    """
    result = []
    for fam in FilteredElementCollector(doc).OfClass(Family):
        cat = fam.FamilyCategory
        if cat is None or cat.Id.IntegerValue != _OST_ROOM_TAGS:
            continue
        for sid in fam.GetFamilySymbolIds():
            sym = doc.GetElement(sid)
            if sym is not None:
                result.append(sym)
    return result


def tag_type_label(sym):
    """«Семейство : Типоразмер» для показа в списке выбора."""
    try:
        fam_name = sym.Family.Name
    except Exception:
        fam_name = u"?"
    try:
        type_name = Element.Name.GetValue(sym)
    except Exception:
        type_name = unicode(sym.Id.IntegerValue)
    return u"{} : {}".format(fam_name, type_name)


# ---------------------------------------------------------------------------
# Подложка вида
# ---------------------------------------------------------------------------

def _suppress_underlay(view):
    """
    Снять подложку с вида (мешает вставке марок помещений). Возвращает
    (base_id, top_id) для последующего восстановления либо None, если
    подложки нет / вид её не поддерживает.
    """
    try:
        base = view.GetUnderlayBaseLevel()
    except Exception:
        return None
    if base is None or base == ElementId.InvalidElementId:
        return None
    try:
        top = view.GetUnderlayTopLevel()
    except Exception:
        top = ElementId.InvalidElementId
    try:
        view.SetUnderlayBaseLevel(ElementId.InvalidElementId)
    except Exception:
        return None
    return (base, top)


def _restore_underlay(view, saved):
    if not saved:
        return
    base, top = saved
    try:
        view.SetUnderlayBaseLevel(base)
    except Exception:
        return
    if top is not None and top != ElementId.InvalidElementId:
        try:
            view.SetUnderlayTopLevel(top)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Помещения связей
# ---------------------------------------------------------------------------

def _iter_link_rooms(doc):
    """(link_instance, total_transform, room) по всем подключённым связям."""
    for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        linked_doc = link.GetLinkDocument()
        if linked_doc is None:
            continue
        transform = link.GetTotalTransform()
        rooms = FilteredElementCollector(linked_doc) \
            .OfCategory(BuiltInCategory.OST_Rooms) \
            .WhereElementIsNotElementType() \
            .ToElements()
        for room in rooms:
            yield link, transform, room


def _room_placed(room):
    try:
        return room.Area > 0 and room.Location is not None
    except Exception:
        return False


def _room_center_host(room, transform):
    """Центр помещения в координатах хоста (точка расположения либо центр
    габаритного бокса). None, если ничего не удалось получить."""
    try:
        loc = room.Location
        if isinstance(loc, LocationPoint):
            return transform.OfPoint(loc.Point)
    except Exception:
        pass
    try:
        bbox = room.get_BoundingBox(None)
        if bbox is not None:
            return transform.OfPoint((bbox.Min + bbox.Max) * 0.5)
    except Exception:
        pass
    return None


def _find_link_room_at(doc, host_point):
    """
    Помещение связи под точкой host_point (координаты хоста). Сначала
    точное IsPointInRoom, иначе ближайшее по центру среди тех, чей
    габаритный бокс (расширенный на _NEAR_TOL) накрывает точку.
    Возвращает (link, transform, room) или None.
    """
    candidates = []
    for link in FilteredElementCollector(doc).OfClass(RevitLinkInstance):
        linked_doc = link.GetLinkDocument()
        if linked_doc is None:
            continue
        transform = link.GetTotalTransform()
        local = transform.Inverse.OfPoint(host_point)
        rooms = FilteredElementCollector(linked_doc) \
            .OfCategory(BuiltInCategory.OST_Rooms) \
            .WhereElementIsNotElementType() \
            .ToElements()
        for room in rooms:
            try:
                if room.Area <= 0:
                    continue
            except Exception:
                continue
            try:
                if room.IsPointInRoom(local):
                    return (link, transform, room)
            except Exception:
                pass
            try:
                bbox = room.get_BoundingBox(None)
            except Exception:
                bbox = None
            if bbox is None:
                continue
            if (bbox.Min.X - _NEAR_TOL_FT <= local.X <= bbox.Max.X + _NEAR_TOL_FT and
                    bbox.Min.Y - _NEAR_TOL_FT <= local.Y <= bbox.Max.Y + _NEAR_TOL_FT and
                    bbox.Min.Z - _NEAR_TOL_FT <= local.Z <= bbox.Max.Z + _NEAR_TOL_FT):
                center = (bbox.Min + bbox.Max) * 0.5
                candidates.append((center.DistanceTo(local), link, transform, room))

    if candidates:
        candidates.sort(key=lambda item: item[0])
        _, link, transform, room = candidates[0]
        return (link, transform, room)
    return None


# ---------------------------------------------------------------------------
# Существующие марки на виде
# ---------------------------------------------------------------------------

def _tagged_room_key(tag):
    """(link_instance_id, linked_room_id) как кортеж int, либо None."""
    try:
        leid = tag.TaggedRoomId
    except Exception:
        return None
    if leid is None:
        return None
    try:
        link_id = leid.LinkInstanceId
        if link_id is not None and link_id != ElementId.InvalidElementId:
            return (link_id.IntegerValue, leid.LinkedElementId.IntegerValue)
        return (0, leid.HostElementId.IntegerValue)
    except Exception:
        return None


def _resolve_tagged_room(doc, tag):
    """Сам элемент Room, на который смотрит марка, либо None (мёртвая
    ссылка на удалённое помещение связи)."""
    try:
        leid = tag.TaggedRoomId
    except Exception:
        return None
    if leid is None:
        return None
    try:
        link_id = leid.LinkInstanceId
    except Exception:
        link_id = None
    if link_id is not None and link_id != ElementId.InvalidElementId:
        link = doc.GetElement(link_id)
        linked_doc = link.GetLinkDocument() if link is not None else None
        if linked_doc is None:
            return None
        try:
            return linked_doc.GetElement(leid.LinkedElementId)
        except Exception:
            return None
    try:
        return doc.GetElement(leid.HostElementId)
    except Exception:
        return None


def _is_stale(doc, tag):
    try:
        if tag.IsOrphaned:
            return True
    except Exception:
        pass
    return _resolve_tagged_room(doc, tag) is None


def _recreate_stale_tag(doc, view, old_tag, tag_type_id):
    """Поставить новую марку на место мёртвой, привязав к помещению связи
    под головой старой марки, и удалить старую. Новая создаётся до
    удаления старой — если помещение не нашлось или создать не удалось,
    старая марка остаётся на месте. Возвращает новую марку либо None."""
    try:
        head = old_tag.TagHeadPosition
    except Exception:
        head = None
    if head is None:
        return None

    found = _find_link_room_at(doc, head)
    if found is None:
        return None
    link, transform, room = found

    center = _room_center_host(room, transform)
    if center is None:
        return None

    try:
        new_tag = doc.Create.NewRoomTag(
            LinkElementId(link.Id, room.Id), UV(center.X, center.Y), view.Id)
    except Exception:
        new_tag = None
    if new_tag is None:
        return None

    try:
        had_leader = old_tag.HasLeader
    except Exception:
        had_leader = False

    try:
        doc.Delete(old_tag.Id)
    except Exception:
        pass

    _apply_type(new_tag, tag_type_id)
    try:
        new_tag.HasLeader = had_leader
    except Exception:
        pass
    try:
        new_tag.TagHeadPosition = head
    except Exception:
        pass
    return new_tag


def _apply_type(tag, tag_type_id):
    try:
        if tag.GetTypeId() != tag_type_id:
            tag.ChangeTypeId(tag_type_id)
            return True
    except Exception:
        pass
    return False


def _point_in_view_crop(view, point):
    try:
        if not view.CropBoxActive:
            return True
        crop = view.CropBox
        local = crop.Transform.Inverse.OfPoint(point)
        return (crop.Min.X <= local.X <= crop.Max.X and
                crop.Min.Y <= local.Y <= crop.Max.Y)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Главный проход
# ---------------------------------------------------------------------------

def run(doc, view, tag_type_id):
    """
    Обновить и дорасставить марки помещений на ``view``. Транзакцию
    открывает вызывающий. Возвращает словарь-статистику с ключами:
    added, recreated, retyped, already, orphan_unresolved, out_of_view,
    room_no_point.
    """
    stats = {
        "added": 0,
        "recreated": 0,
        "retyped": 0,
        "already": 0,
        "orphan_unresolved": 0,
        "out_of_view": 0,
        "room_no_point": 0,
    }

    saved_underlay = _suppress_underlay(view)
    try:
        try:
            level = view.GenLevel
            view_elev = level.Elevation if level is not None else None
        except Exception:
            view_elev = None

        existing_tags = list(
            FilteredElementCollector(doc, view.Id)
            .OfCategory(BuiltInCategory.OST_RoomTags)
            .WhereElementIsNotElementType()
        )

        tagged_keys = set()

        # 1) Существующие марки: мёртвые — пересоздать, живые — сменить тип.
        for tag in existing_tags:
            if _is_stale(doc, tag):
                new_tag = _recreate_stale_tag(doc, view, tag, tag_type_id)
                if new_tag is None:
                    stats["orphan_unresolved"] += 1
                    continue
                stats["recreated"] += 1
                key = _tagged_room_key(new_tag)
                if key is not None:
                    tagged_keys.add(key)
            else:
                if _apply_type(tag, tag_type_id):
                    stats["retyped"] += 1
                key = _tagged_room_key(tag)
                if key is not None:
                    tagged_keys.add(key)

        # 2) Дорасставить марки для непомеченных помещений связей.
        for link, transform, room in _iter_link_rooms(doc):
            if not _room_placed(room):
                continue
            key = (link.Id.IntegerValue, room.Id.IntegerValue)
            if key in tagged_keys:
                stats["already"] += 1
                continue

            center = _room_center_host(room, transform)
            if center is None:
                stats["room_no_point"] += 1
                continue
            if view_elev is not None and abs(center.Z - view_elev) > _LEVEL_TOL_FT:
                continue
            if not _point_in_view_crop(view, center):
                stats["out_of_view"] += 1
                continue

            try:
                new_tag = doc.Create.NewRoomTag(
                    LinkElementId(link.Id, room.Id),
                    UV(center.X, center.Y), view.Id)
            except Exception:
                new_tag = None
            if new_tag is None:
                continue
            _apply_type(new_tag, tag_type_id)
            stats["added"] += 1
    finally:
        _restore_underlay(view, saved_underlay)

    return stats
