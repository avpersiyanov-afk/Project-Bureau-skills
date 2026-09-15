# -*- coding: utf-8 -*-

__title__ = u"Зум к\nэлементу"
__doc__ = (
    u"Приближает вид к элементу. Если элемент уже выбран в модели — просто "
    u"зумирует к нему (к нескольким выбранным — к их общему габариту). Если "
    u"ничего не выбрано — спрашивает ID элемента и зумирует к нему. Если "
    u"элемента не видно на активном виде, подсказывает, на какой уровень "
    u"(план/разрез/3D) переключиться, чтобы он был заметен."
)
__author__ = "Pipers"

import clr

clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import (
    BuiltInParameter,
    ElementId,
    FilteredElementCollector,
    Level,
    LocationCurve,
    LocationPoint,
    XYZ,
)
from System.Collections.Generic import List

from pyrevit import revit, forms

doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView

MM_IN_FOOT = 304.8

# Параметры, в которых у разных категорий лежит ссылка на уровень элемента —
# перебираем по очереди, если у элемента нет прямого свойства LevelId.
LEVEL_BIPS = (
    BuiltInParameter.FAMILY_LEVEL_PARAM,
    BuiltInParameter.SCHEDULE_LEVEL_PARAM,
    BuiltInParameter.INSTANCE_SCHEDULE_ONLY_LEVEL_PARAM,
    BuiltInParameter.INSTANCE_REFERENCE_LEVEL_PARAM,
    BuiltInParameter.FAMILY_BASE_LEVEL_PARAM,
    BuiltInParameter.RBS_START_LEVEL_PARAM,
    BuiltInParameter.LEVEL_PARAM,
)


def get_selected_elements():
    ids = list(uidoc.Selection.GetElementIds())
    return [doc.GetElement(eid) for eid in ids if doc.GetElement(eid) is not None]


def ask_element_by_id():
    raw = forms.ask_for_string(
        default="",
        prompt=u"Введите ID элемента, к которому нужно приблизить вид:",
        title=u"Зум к элементу"
    )

    if raw is None:
        # Пользователь закрыл окно — тихо выходим.
        forms.alert(u"Операция отменена.", exitscript=True)

    raw = raw.strip()
    if not raw:
        forms.alert(u"ID не введён.", exitscript=True)

    try:
        int_id = int(raw)
    except ValueError:
        forms.alert(u"«{}» — это не число. ID элемента должен быть целым числом.".format(raw), exitscript=True)

    el = doc.GetElement(ElementId(int_id))
    if el is None:
        forms.alert(
            u"Элемент с ID {} не найден в текущем документе. Возможно, он "
            u"удалён или находится в связанной модели.".format(int_id),
            exitscript=True
        )

    return el


def get_level_name(el):
    """Имя уровня элемента: свойство LevelId -> типовые параметры -> ближайший уровень по Z."""
    try:
        lid = el.LevelId
        if lid is not None and lid != ElementId.InvalidElementId:
            lvl = doc.GetElement(lid)
            if lvl is not None:
                return lvl.Name
    except:
        pass

    for bip in LEVEL_BIPS:
        try:
            p = el.get_Parameter(bip)
            if p and p.HasValue:
                lvl = doc.GetElement(p.AsElementId())
                if lvl is not None:
                    return lvl.Name
        except:
            pass

    # Ближайший уровень по средней отметке габаритного контейнера элемента.
    try:
        bb = el.get_BoundingBox(None)
        if bb is not None:
            z = (bb.Min.Z + bb.Max.Z) / 2.0
            levels = list(FilteredElementCollector(doc).OfClass(Level))
            if levels:
                nearest = min(levels, key=lambda L: abs(L.Elevation - z))
                return nearest.Name
    except:
        pass

    return None


def get_elevation_m(el):
    try:
        bb = el.get_BoundingBox(None)
        if bb is not None:
            return (bb.Min.Z + bb.Max.Z) / 2.0 * MM_IN_FOOT / 1000.0
    except:
        pass
    return None


def _bbox_world_center(bb):
    """Центр габаритного контейнера в МИРОВЫХ координатах.

    У BoundingBoxXYZ есть собственный Transform: у большинства элементов он
    единичный, но у части категорий (импорты, связи, некоторые семейства)
    Min/Max заданы в локальной системе контейнера, и без применения
    Transform центр оказывается «влево-вниз» или вообще далеко за краем.
    Transform аффинный, поэтому достаточно применить его к середине
    (Min+Max)/2 — это и есть мировой центр.
    """
    mid = XYZ((bb.Min.X + bb.Max.X) / 2.0,
              (bb.Min.Y + bb.Max.Y) / 2.0,
              (bb.Min.Z + bb.Max.Z) / 2.0)
    try:
        return bb.Transform.OfPoint(mid)
    except:
        return mid


def build_visible_ids():
    """
    IntegerValue всех элементов, которые Revit реально показывает на
    активном виде (FilteredElementCollector по Id вида учитывает видимость
    категорий, фильтры, диапазон вида, скрытые элементы). Это надёжнее, чем
    судить о видимости по наличию get_BoundingBox(view): у части семейств
    (символьных, без 3D-геометрии в плане) габарит на виде None, хотя сам
    элемент на виде виден.
    """
    try:
        return set(
            x.IntegerValue
            for x in FilteredElementCollector(doc, view.Id)
            .WhereElementIsNotElementType()
            .ToElementIds()
        )
    except:
        return None


VISIBLE_IDS = build_visible_ids()


def is_visible(el):
    try:
        if VISIBLE_IDS is not None and el.Id.IntegerValue in VISIBLE_IDS:
            return True
    except:
        pass
    try:
        return el.get_BoundingBox(view) is not None
    except:
        return False


def element_target_point(el):
    """
    Мировая точка элемента для зума, или None если определить не удалось.

    Приоритет: точка расположения (LocationPoint) -> середина оси
    (LocationCurve) -> мировой центр габарита (на активном виде, иначе
    модельного). LocationPoint/ось берём как есть — для подавляющего
    большинства семейств это и есть их видимое место; проверку «далеко ли
    от габарита» убрали, потому что у символьных семейств габарит на виде
    как раз мелкий/смещённый, и из-за той проверки зум уходил в пустоту.
    """
    loc = getattr(el, "Location", None)
    if isinstance(loc, LocationPoint):
        try:
            return loc.Point
        except:
            pass
    elif isinstance(loc, LocationCurve):
        try:
            return loc.Curve.Evaluate(0.5, True)
        except:
            pass

    for v in (view, None):
        try:
            bb = el.get_BoundingBox(v)
        except:
            bb = None
        if bb is not None:
            return _bbox_world_center(bb)

    return None


def zoom_center_on_points(points, pad_ft=6.0):
    """
    Ставит облако points (список XYZ) в центр активного вида.

    Отличие от route_preview.zoom_to_fit_points: тот прямоугольник для
    маршрута всегда широкий и вытянутый — близко к соотношению сторон
    экрана, поэтому ZoomAndCenterRectangle центрирует его точно. Для одного
    (или нескольких рядом) элемента прямоугольник почти квадратный,
    расхождение с экраном максимальное — и ZoomAndCenterRectangle
    центрирует с заметным смещением. Поэтому здесь прямоугольник заранее
    расширяется до соотношения сторон окна вида (GetWindowRectangle) —
    тогда центрирование точное.
    """
    pts = [p for p in points if p is not None]
    if not pts:
        return

    uiview = None
    for uv in uidoc.GetOpenUIViews():
        if uv.ViewId == view.Id:
            uiview = uv
            break
    if uiview is None:
        return

    min_x = min(p.X for p in pts)
    max_x = max(p.X for p in pts)
    min_y = min(p.Y for p in pts)
    max_y = max(p.Y for p in pts)
    z = sum(p.Z for p in pts) / len(pts)

    cx = (min_x + max_x) / 2.0
    cy = (min_y + max_y) / 2.0
    half_x = (max_x - min_x) / 2.0 + pad_ft
    half_y = (max_y - min_y) / 2.0 + pad_ft

    try:
        rect = uiview.GetWindowRectangle()
        win_w = abs(rect.Right - rect.Left)
        win_h = abs(rect.Bottom - rect.Top)
        if win_w > 0 and win_h > 0:
            aspect = float(win_w) / float(win_h)
            if half_x / half_y < aspect:
                half_x = half_y * aspect
            else:
                half_y = half_x / aspect
    except:
        pass

    try:
        uiview.ZoomAndCenterRectangle(
            XYZ(cx - half_x, cy - half_y, z),
            XYZ(cx + half_x, cy + half_y, z),
        )
    except:
        pass
    try:
        uidoc.RefreshActiveView()
    except:
        pass


def hint_where_to_look(elements):
    el = elements[0]
    level_name = get_level_name(el)
    elev_m = get_elevation_m(el)

    active_level = getattr(view, "GenLevel", None)
    active_level_name = active_level.Name if active_level is not None else None

    elev_line = u"\nОтметка элемента ≈ {:.2f} м.".format(elev_m) if elev_m is not None else u""

    if level_name and active_level_name and level_name == active_level_name:
        forms.alert(
            u"Элемент относится к уровню активного вида («{}»), но на виде "
            u"не показан. Проверьте: видимость его категории (VG), фильтры "
            u"вида, стадию (Phase) и диапазон вида (View Range) — элемент "
            u"может быть выше/ниже секущей плоскости.{}".format(level_name, elev_line),
            title=u"Зум к элементу"
        )
    elif level_name:
        forms.alert(
            u"Элемента не видно на активном виде. Похоже, он расположен на "
            u"уровне «{}». Переключитесь на план этого уровня (или на "
            u"подходящий разрез / 3D-вид) и запустите кнопку снова.{}".format(level_name, elev_line),
            title=u"Зум к элементу"
        )
    else:
        forms.alert(
            u"Элемента не видно на активном виде, и определить его уровень "
            u"не удалось. Откройте 3D-вид или разрез, где элемент виден, и "
            u"запустите кнопку снова.{}".format(elev_line),
            title=u"Зум к элементу"
        )


# ------------------------------------------------------------
# ОСНОВНОЙ СЦЕНАРИЙ
# ------------------------------------------------------------

elements = get_selected_elements()

if not elements:
    el = ask_element_by_id()
    elements = [el]
    # Выделяем найденный по ID элемент, чтобы его было видно после зума.
    sel_ids = List[ElementId]()
    sel_ids.Add(el.Id)
    try:
        uidoc.Selection.SetElementIds(sel_ids)
    except:
        pass

# Точки для зума — только по элементам, которые видны на активном виде.
points = []
any_visible = False
for el in elements:
    if not is_visible(el):
        continue
    any_visible = True
    pt = element_target_point(el)
    if pt is not None:
        points.append(pt)

if not any_visible:
    # Ни один из элементов не виден на активном виде — подсказываем куда смотреть.
    hint_where_to_look(elements)
elif not points:
    forms.alert(
        u"Элемент(ы) на виде есть, но определить его положение для зума не "
        u"удалось.",
        title=u"Зум к элементу"
    )
else:
    zoom_center_on_points(points)
