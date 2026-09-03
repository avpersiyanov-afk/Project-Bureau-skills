# -*- coding: utf-8 -*-

__title__ = "Размеры\nосей"
__doc__ = "Образмеривает выбранные оси на виде"
__author__ = "Pipers"

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')

from Autodesk.Revit.DB import *
from pyrevit import revit, forms


doc = revit.doc
uidoc = revit.uidoc
view = doc.ActiveView


# ------------------------------------------------------------
# НАСТРОЙКИ
# ------------------------------------------------------------

# Отступ размерной цепочки от осей, мм
OFFSET_MM = 1400.0

# Отступ общего размера от размерной цепочки, мм
TOTAL_GAP_MM = 700.0

MM_IN_FOOT = 304.8

OFFSET = OFFSET_MM / MM_IN_FOOT
TOTAL_OFFSET = (OFFSET_MM + TOTAL_GAP_MM) / MM_IN_FOOT


# ------------------------------------------------------------
# ФУНКЦИИ
# ------------------------------------------------------------

def get_dim_type_name(dim_type):
    """Получить имя типа размера"""
    try:
        param = dim_type.get_Parameter(BuiltInParameter.SYMBOL_NAME_PARAM)
        if param:
            return param.AsString()
    except:
        pass

    try:
        return dim_type.Name
    except:
        return str(dim_type.Id.IntegerValue)


class DimTypeOption(object):
    """Обертка для отображения типа размера в списке"""

    def __init__(self, dim_type):
        self.dim_type = dim_type
        self.name = get_dim_type_name(dim_type)

    def __str__(self):
        return self.name


def get_selected_grids():
    """Получить выбранные оси"""
    selected_ids = uidoc.Selection.GetElementIds()

    grids = []

    for el_id in selected_ids:
        el = doc.GetElement(el_id)
        if isinstance(el, Grid):
            grids.append(el)

    return grids


def get_linear_dimension_types():
    """Получить линейные стили размеров"""
    result = []

    collector = FilteredElementCollector(doc).OfClass(DimensionType)

    for dim_type in collector:
        try:
            if dim_type.StyleType == DimensionStyleType.Linear:
                result.append(dim_type)
        except:
            pass

    return result


def choose_dimension_type():
    """Окно выбора стиля размеров"""
    dim_types = get_linear_dimension_types()

    if not dim_types:
        forms.alert(
            "В проекте не найдены линейные стили размеров.",
            exitscript=True
        )

    options = [DimTypeOption(x) for x in dim_types]
    options = sorted(options, key=lambda x: x.name)

    selected = forms.SelectFromList.show(
        options,
        title="Выберите стиль размеров",
        button_name="Создать размеры",
        multiselect=False
    )

    if not selected:
        forms.alert("Операция отменена.", exitscript=True)

    return selected.dim_type


def is_vertical_grid(grid):
    """
    True  — вертикальная ось
    False — горизонтальная ось
    """
    curve = grid.Curve

    p1 = curve.GetEndPoint(0)
    p2 = curve.GetEndPoint(1)

    dx = abs(p2.X - p1.X)
    dy = abs(p2.Y - p1.Y)

    return dy >= dx


def sort_grids(grids, vertical):
    """Сортировка осей"""

    def key_func(grid):
        curve = grid.Curve
        p1 = curve.GetEndPoint(0)
        p2 = curve.GetEndPoint(1)

        if vertical:
            return (p1.X + p2.X) / 2.0
        else:
            return (p1.Y + p2.Y) / 2.0

    return sorted(grids, key=key_func)


def apply_dimension_type(dimension, dim_type):
    """Применить выбранный стиль размера"""
    if dimension and dim_type:
        dimension.ChangeTypeId(dim_type.Id)


def get_grid_center_x(grid):
    curve = grid.Curve
    p1 = curve.GetEndPoint(0)
    p2 = curve.GetEndPoint(1)
    return (p1.X + p2.X) / 2.0


def get_grid_center_y(grid):
    curve = grid.Curve
    p1 = curve.GetEndPoint(0)
    p2 = curve.GetEndPoint(1)
    return (p1.Y + p2.Y) / 2.0


def create_dimension_chain(grids, vertical, dim_type, offset):
    """
    Создать размерную цепочку по всем осям
    """

    if len(grids) < 2:
        return None

    refs = ReferenceArray()

    for grid in grids:
        refs.Append(Reference(grid))

    points = []

    for grid in grids:
        curve = grid.Curve
        points.append(curve.GetEndPoint(0))
        points.append(curve.GetEndPoint(1))

    z = points[0].Z

    if vertical:
        # Вертикальные оси образмериваются горизонтальной размерной линией

        x_values = [get_grid_center_x(g) for g in grids]

        y_min = min([p.Y for p in points])
        y_dim = y_min - offset

        start = XYZ(min(x_values), y_dim, z)
        end = XYZ(max(x_values), y_dim, z)

    else:
        # Горизонтальные оси образмериваются вертикальной размерной линией

        y_values = [get_grid_center_y(g) for g in grids]

        x_min = min([p.X for p in points])
        x_dim = x_min - offset

        start = XYZ(x_dim, min(y_values), z)
        end = XYZ(x_dim, max(y_values), z)

    dim_line = Line.CreateBound(start, end)

    dimension = doc.Create.NewDimension(view, dim_line, refs)
    apply_dimension_type(dimension, dim_type)

    return dimension


def create_total_dimension(grids, vertical, dim_type, offset):
    """
    Создать общий размер между первой и последней осью
    """

    if len(grids) < 3:
        # Если осей только 2, общий размер совпадает с цепочкой
        return None

    first_grid = grids[0]
    last_grid = grids[-1]

    refs = ReferenceArray()
    refs.Append(Reference(first_grid))
    refs.Append(Reference(last_grid))

    points = []

    for grid in grids:
        curve = grid.Curve
        points.append(curve.GetEndPoint(0))
        points.append(curve.GetEndPoint(1))

    z = points[0].Z

    if vertical:
        # Общий размер для вертикальных осей

        x1 = get_grid_center_x(first_grid)
        x2 = get_grid_center_x(last_grid)

        y_min = min([p.Y for p in points])
        y_dim = y_min - offset

        start = XYZ(x1, y_dim, z)
        end = XYZ(x2, y_dim, z)

    else:
        # Общий размер для горизонтальных осей

        y1 = get_grid_center_y(first_grid)
        y2 = get_grid_center_y(last_grid)

        x_min = min([p.X for p in points])
        x_dim = x_min - offset

        start = XYZ(x_dim, y1, z)
        end = XYZ(x_dim, y2, z)

    dim_line = Line.CreateBound(start, end)

    dimension = doc.Create.NewDimension(view, dim_line, refs)
    apply_dimension_type(dimension, dim_type)

    return dimension


# ------------------------------------------------------------
# ОСНОВНОЙ КОД
# ------------------------------------------------------------

grids = get_selected_grids()

if not grids:
    forms.alert(
        "Сначала выберите оси в Revit, потом запустите кнопку.",
        exitscript=True
    )

if len(grids) < 2:
    forms.alert(
        "Выберите минимум две оси.",
        exitscript=True
    )

dim_type = choose_dimension_type()

vertical_grids = []
horizontal_grids = []

for grid in grids:
    if is_vertical_grid(grid):
        vertical_grids.append(grid)
    else:
        horizontal_grids.append(grid)

created_chain = []
created_total = []

with revit.Transaction("Create Grid Dimensions"):

    # Вертикальные оси
    if len(vertical_grids) >= 2:
        vertical_grids = sort_grids(vertical_grids, True)

        dim_chain = create_dimension_chain(
            vertical_grids,
            True,
            dim_type,
            OFFSET
        )

        if dim_chain:
            created_chain.append(dim_chain)

        dim_total = create_total_dimension(
            vertical_grids,
            True,
            dim_type,
            TOTAL_OFFSET
        )

        if dim_total:
            created_total.append(dim_total)

    # Горизонтальные оси
    if len(horizontal_grids) >= 2:
        horizontal_grids = sort_grids(horizontal_grids, False)

        dim_chain = create_dimension_chain(
            horizontal_grids,
            False,
            dim_type,
            OFFSET
        )

        if dim_chain:
            created_chain.append(dim_chain)

        dim_total = create_total_dimension(
            horizontal_grids,
            False,
            dim_type,
            TOTAL_OFFSET
        )

        if dim_total:
            created_total.append(dim_total)


forms.alert(
    "Готово.\n\n"
    "Размерных цепочек: {}\n"
    "Общих размеров: {}\n\n"
    "Отступ цепочки: {} мм\n"
    "Отступ общего размера: {} мм".format(
        len(created_chain),
        len(created_total),
        int(OFFSET_MM),
        int(OFFSET_MM + TOTAL_GAP_MM)
    )
)
