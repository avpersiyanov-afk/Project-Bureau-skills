# -*- coding: utf-8 -*-
"""Общие геометрические хелперы для работы с элементами Revit."""

from Autodesk.Revit.DB import LocationPoint, LocationCurve, Line, Level, FilteredElementCollector, ElementId, XYZ


def get_point(el):
    """Точка расположения элемента (для point-based экземпляров)."""
    try:
        if isinstance(el.Location, LocationPoint):
            return el.Location.Point
    except:
        pass
    return None


def get_curve_data(el, view):
    """
    Кривая элемента и её концевые точки, в МИРОВЫХ координатах.

    Curve.GetEndPoint() у некоторых line-based Generic Model семейств
    возвращает точки в системе координат, относительной к рабочей
    плоскости/уровню-хосту (Z там может быть 0, даже если элемент
    физически виден на большой высоте) — поэтому Z корректируется
    смещением между этой относительной кривой и реальным мировым
    bounding box элемента (тоже в мировых координатах, не зависит от
    того, как хранится геометрия внутри семейства).

    Если у элемента нет LocationCurve вовсе — используется диагональ
    bounding box как резервный вариант (уже в мировых координатах).
    """
    try:
        if isinstance(el.Location, LocationCurve):
            c = el.Location.Curve
            p1 = c.GetEndPoint(0)
            p2 = c.GetEndPoint(1)

            try:
                bbox = el.get_BoundingBox(None)
                if bbox is not None:
                    curve_min_z = min(p1.Z, p2.Z)
                    z_offset = bbox.Min.Z - curve_min_z
                    if abs(z_offset) > 1e-6:
                        p1 = XYZ(p1.X, p1.Y, p1.Z + z_offset)
                        p2 = XYZ(p2.X, p2.Y, p2.Z + z_offset)
                        c = Line.CreateBound(p1, p2)
            except:
                pass

            return c, p1, p2
    except:
        pass
    try:
        bbox = el.get_BoundingBox(view)
        if bbox:
            return Line.CreateBound(bbox.Min, bbox.Max), bbox.Min, bbox.Max
    except:
        pass
    return None, None, None


def point_key(p, tol):
    """Ключ для группировки близких точек по сетке с шагом tol."""
    return (
        int(round(p.X / tol)),
        int(round(p.Y / tol)),
        int(round(p.Z / tol))
    )


def points_close(p1, p2, tol):
    try:
        return p1.DistanceTo(p2) <= tol
    except:
        return False


def is_point_on_curve(curve, pt, tol):
    try:
        proj = curve.Project(pt)
        if proj is None:
            return False, None
        p = proj.XYZPoint
        return p.DistanceTo(pt) <= tol, p
    except:
        return False, None


def sort_points(curve, points):
    """Сортировка точек по расстоянию от начала кривой."""
    try:
        p0 = curve.GetEndPoint(0)
        return sorted(points, key=lambda p: p0.DistanceTo(p))
    except:
        return points


def get_document_levels(doc):
    """Все уровни документа, отсортированные по высоте."""
    levels = list(FilteredElementCollector(doc).OfClass(Level))
    return sorted(levels, key=lambda lv: lv.Elevation)


def find_level_for_elevation(z, sorted_levels):
    """
    Уровень, на котором физически находится точка с высотой z: ближайший
    снизу (Elevation <= z), иначе — самый нижний уровень из sorted_levels.
    sorted_levels — результат get_document_levels(doc) (уже отсортирован).
    """
    if not sorted_levels:
        return None

    best = sorted_levels[0]

    for lv in sorted_levels:
        if lv.Elevation <= z:
            best = lv
        else:
            break

    return best


def get_element_level(doc, el):
    """
    Уровень, связанный с элементом, через Element.LevelId — это работает
    и для параметра «Уровень» экземпляра (устройства/панели), и для
    рабочей плоскости линейного элемента (для линий, размещённых по
    рабочей плоскости уровня, LevelId ссылается именно на неё), не завися
    от языка интерфейса Revit (в отличие от поиска параметра по имени).
    Возвращает None, если у элемента нет связанного уровня.
    """
    try:
        level_id = el.LevelId
    except:
        return None

    if not level_id or level_id == ElementId.InvalidElementId:
        return None

    try:
        return doc.GetElement(level_id)
    except:
        return None
