# -*- coding: utf-8 -*-
"""
Тело кнопки `Tools.panel/RenameByList` («Переименование по списку»).

Сценарий: пользователь выбирает папку, внутри которой лежат две подпапки
с файлами (например DWG и PDF выгрузки листов) — файлы в них называются
одинаково (без учёта расширения), например «5.2.dwg» и «5.2.pdf». Кнопка
собирает объединённый список таких имён (без расширения) из обеих
подпапок первого уровня и показывает таблицу: слева — текущее имя, справа
— редактируемое поле для нового имени. По ОК каждый файл, чьё имя без
расширения совпадает с изменённой строкой таблицы, переименовывается
(расширение сохраняется) — обход идёт по ВСЕЙ выбранной папке и её
подпапкам, а не только по тем двум, откуда собирался список, на случай
если совпадающие по имени файлы есть и глубже/рядом.

Ничего в модели Revit не меняется — только `os.rename` на диске.
"""

import os

import clr
clr.AddReference('System')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from System import Object
from System.Collections.Generic import List

from pyrevit import forms

from System.Windows import (
    Window, WindowStartupLocation, Thickness, FontWeights,
    HorizontalAlignment, TextWrapping
)
from System.Windows.Controls import (
    StackPanel, TextBlock, Button, Orientation, DockPanel, Dock,
    DataGrid, DataGridTextColumn, DataGridLength, DataGridLengthUnitType,
    DataGridHeadersVisibility, DataGridGridLinesVisibility, DataGridSelectionMode
)
from System.Windows.Data import Binding, BindingMode, UpdateSourceTrigger


# Недопустимые в имени файла символы (Windows).
INVALID_FILENAME_CHARS = u'\\/:*?"<>|'


# --- поиск подпапок и сбор имён ------------------------------------------------

def _subfolders(root):
    try:
        names = sorted(os.listdir(root))
    except Exception:
        return []
    return [os.path.join(root, n) for n in names
            if os.path.isdir(os.path.join(root, n))]


def _pick_two_subfolders(root):
    """Ровно две подпапки первого уровня внутри root — сами (если их две)
    или выбором пользователя (если больше). None + alert, если меньше двух
    или выбор не удался."""
    subs = _subfolders(root)
    if len(subs) < 2:
        forms.alert(
            u"В папке:\n{}\n\nдолжно быть минимум две подпапки с файлами. "
            u"Найдено: {}.".format(root, len(subs)),
            title=u"Переименование по списку",
        )
        return None
    if len(subs) == 2:
        return subs

    names = [os.path.basename(p) for p in subs]
    picked = forms.SelectFromList.show(
        names,
        title=u"В папке больше двух подпапок — выберите ровно две с парными файлами",
        button_name=u"Выбрать",
        multiselect=True,
    )
    if not picked or len(picked) != 2:
        forms.alert(u"Нужно выбрать ровно две подпапки.",
                    title=u"Переименование по списку")
        return None
    return [os.path.join(root, n) for n in picked]


def _stems(folder):
    """Множество имён файлов без расширения (только этот уровень папки)."""
    stems = set()
    try:
        names = os.listdir(folder)
    except Exception:
        return stems
    for name in names:
        path = os.path.join(folder, name)
        if os.path.isfile(path):
            stem, _ext = os.path.splitext(name)
            if stem:
                stems.add(stem)
    return stems


def collect_names(root):
    """(stems, folder_a, folder_b) — объединение имён (без расширения) из
    двух подпапок первого уровня в root, либо None (с alert)."""
    subs = _pick_two_subfolders(root)
    if not subs:
        return None
    folder_a, folder_b = subs[0], subs[1]
    stems = _stems(folder_a) | _stems(folder_b)
    if not stems:
        forms.alert(
            u"В подпапках:\n{}\n{}\n\nне найдено файлов.".format(folder_a, folder_b),
            title=u"Переименование по списку",
        )
        return None
    return sorted(stems, key=lambda s: s.lower()), folder_a, folder_b


# --- планирование и применение переименования ----------------------------------

def plan_renames(root, mapping):
    """mapping: {старое_имя_без_расширения: новое_имя}. Обходит root
    рекурсивно; для каждого файла, чьё имя без расширения совпадает с
    ключом mapping, планирует переименование (расширение файла
    сохраняется). Возвращает [(src, dst, status)], status "ok"/"collision"."""
    plans = []
    if not mapping:
        return plans
    for cur_root, _dirs, files in os.walk(root):
        for name in files:
            stem, ext = os.path.splitext(name)
            new_stem = mapping.get(stem)
            if not new_stem or new_stem == stem:
                continue
            src = os.path.join(cur_root, name)
            dst = os.path.join(cur_root, new_stem + ext)
            if os.path.exists(dst):
                plans.append((src, dst, "collision"))
            else:
                plans.append((src, dst, "ok"))
    plans.sort(key=lambda p: p[0].lower())
    return plans


def apply_renames(plans):
    """Переименовывает пары со статусом "ok". Возвращает (renamed, errors)."""
    renamed = 0
    errors = []
    for src, dst, status in plans:
        if status != "ok":
            continue
        try:
            os.rename(src, dst)
            renamed += 1
        except Exception as exc:
            errors.append((src, dst, unicode(exc)))
    return renamed, errors


def _valid_name(name):
    return not any(ch in name for ch in INVALID_FILENAME_CHARS)


# --- окно со списком имён (WPF DataGrid, см. family_catalog.show_status_form) --

class _NameRow(object):
    def __init__(self, old_name):
        self.OldName = old_name
        self.NewName = old_name


def _star(n):
    return DataGridLength(n, DataGridLengthUnitType.Star)


def _show_rename_dialog(stems, folder_a, folder_b):
    """Таблица «текущее имя / новое имя» (второй столбец редактируемый).
    Возвращает dict {старое: новое} только для изменённых строк, либо None
    при отмене."""
    data = List[Object]()
    for stem in stems:
        data.Add(_NameRow(stem))

    result = {"mapping": None}

    win = Window()
    win.Title = u"Переименование по списку"
    win.Width = 640
    win.Height = 620
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen

    outer = DockPanel()
    outer.LastChildFill = True

    header = StackPanel()
    header.Margin = Thickness(16, 12, 16, 8)
    DockPanel.SetDock(header, Dock.Top)

    title = TextBlock()
    title.Text = u"Список имён (без расширения)"
    title.FontSize = 16
    title.FontWeight = FontWeights.Bold
    header.Children.Add(title)

    info = TextBlock()
    info.Text = (
        u"Папка A: {}\nПапка B: {}\n\n"
        u"Во втором столбце впишите новое имя. Пустые и неизменённые строки "
        u"пропускаются. ОК переименует файлы с таким именем (без учёта "
        u"расширения) во всей выбранной папке и её подпапках.".format(
            folder_a, folder_b)
    )
    info.FontSize = 11
    info.TextWrapping = TextWrapping.Wrap
    info.Margin = Thickness(0, 4, 0, 0)
    header.Children.Add(info)

    grid = DataGrid()
    grid.Margin = Thickness(16, 0, 16, 0)
    grid.AutoGenerateColumns = False
    grid.CanUserAddRows = False
    grid.CanUserDeleteRows = False
    grid.CanUserResizeRows = False
    grid.HeadersVisibility = DataGridHeadersVisibility.Column
    grid.GridLinesVisibility = DataGridGridLinesVisibility.Horizontal
    grid.SelectionMode = DataGridSelectionMode.Single
    grid.IsReadOnly = False
    grid.ItemsSource = data

    old_col = DataGridTextColumn()
    old_col.Header = u"Текущее имя"
    old_col.Binding = Binding("OldName")
    old_col.IsReadOnly = True
    old_col.Width = _star(1)
    grid.Columns.Add(old_col)

    new_binding = Binding("NewName")
    new_binding.Mode = BindingMode.TwoWay
    new_binding.UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged
    new_col = DataGridTextColumn()
    new_col.Header = u"Новое имя"
    new_col.Binding = new_binding
    new_col.Width = _star(1)
    grid.Columns.Add(new_col)

    bottom = StackPanel()
    bottom.Margin = Thickness(16, 8, 16, 12)
    bottom.Orientation = Orientation.Horizontal
    bottom.HorizontalAlignment = HorizontalAlignment.Right
    DockPanel.SetDock(bottom, Dock.Bottom)

    cancel_btn = Button()
    cancel_btn.Content = u"Отмена"
    cancel_btn.Padding = Thickness(10, 4, 10, 4)
    cancel_btn.Margin = Thickness(0, 0, 8, 0)

    ok_btn = Button()
    ok_btn.Content = u"ОК"
    ok_btn.Padding = Thickness(10, 4, 10, 4)
    ok_btn.FontWeight = FontWeights.Bold

    def on_cancel(sender, args):
        win.Close()

    def on_ok(sender, args):
        try:
            grid.CommitEdit()
        except Exception:
            pass
        mapping = {}
        bad = []
        for row in data:
            new_name = unicode(row.NewName or u"").strip()
            old_name = row.OldName
            if not new_name or new_name == old_name:
                continue
            if not _valid_name(new_name):
                bad.append(old_name)
                continue
            mapping[old_name] = new_name
        if bad:
            forms.alert(
                u"Недопустимые символы в новом имени для: {}\n\n"
                u"Нельзя использовать: {}".format(
                    u", ".join(bad), INVALID_FILENAME_CHARS),
                title=u"Переименование по списку",
            )
            return
        result["mapping"] = mapping
        win.Close()

    cancel_btn.Click += on_cancel
    ok_btn.Click += on_ok

    bottom.Children.Add(cancel_btn)
    bottom.Children.Add(ok_btn)

    outer.Children.Add(header)
    outer.Children.Add(bottom)
    outer.Children.Add(grid)

    win.Content = outer
    win.ShowDialog()

    return result["mapping"]


# --- сценарий кнопки -----------------------------------------------------------

def run():
    try:
        root = forms.pick_folder(title=u"Папка с двумя подпапками для переименования")
    except TypeError:
        root = forms.pick_folder()
    if not root or not os.path.isdir(root):
        return

    collected = collect_names(root)
    if not collected:
        return
    stems, folder_a, folder_b = collected

    mapping = _show_rename_dialog(stems, folder_a, folder_b)
    if mapping is None:
        return
    if not mapping:
        forms.alert(u"Ни одно имя не изменено — переименовывать нечего.",
                    title=u"Переименование по списку")
        return

    plans = plan_renames(root, mapping)
    ok = [p for p in plans if p[2] == "ok"]
    collisions = [p for p in plans if p[2] == "collision"]

    if not ok:
        msg = u"Файлов для переименования не найдено."
        if collisions:
            msg += u"\n\nПропущено (целевое имя уже занято): {}".format(len(collisions))
        forms.alert(msg, title=u"Переименование по списку")
        return

    renamed, errors = apply_renames(ok)

    msg = u"Переименовано файлов: {}".format(renamed)
    if collisions:
        msg += u"\nПропущено (имя уже занято): {}".format(len(collisions))
    if errors:
        msg += u"\nОшибок: {}\n{}".format(
            len(errors),
            u"\n".join(u"  {} — {}".format(os.path.basename(s), m)
                       for s, _d, m in errors[:8]))
    forms.alert(msg, title=u"Переименование по списку")
