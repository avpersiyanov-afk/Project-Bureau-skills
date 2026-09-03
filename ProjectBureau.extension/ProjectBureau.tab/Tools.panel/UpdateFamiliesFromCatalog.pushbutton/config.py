# -*- coding: utf-8 -*-
"""Shift+клик по кнопке «Семейства из каталога» — окно настроек:
папка-каталог, тихий мониторинг при открытии проекта, авто-метка даты
при штатной загрузке семейства + имя видимого параметра для даты."""

import clr
clr.AddReference('PresentationFramework')

from pyrevit import forms

from pbtools import family_catalog as fc

from System.Windows import (
    Window, WindowStartupLocation, Thickness, FontWeights,
    HorizontalAlignment, TextWrapping
)
from System.Windows.Controls import (
    StackPanel, TextBlock, TextBox, CheckBox, Button, Orientation, DockPanel, Dock
)
from System.Windows.Media import Brushes


def _row_label(text, hint=None):
    lbl = TextBlock()
    lbl.Text = text
    lbl.Margin = Thickness(0, 12, 0, 2)
    lbl.FontWeight = FontWeights.Bold
    return lbl


win = Window()
win.Title = u"Семейства из каталога — настройки"
win.Width = 640
win.Height = 440
win.WindowStartupLocation = WindowStartupLocation.CenterScreen

outer = DockPanel()
outer.LastChildFill = True
root_panel = StackPanel()
root_panel.Margin = Thickness(16)

# --- папка каталога ---
root_panel.Children.Add(_row_label(u"Папка-каталог семейств"))
folder_row = StackPanel()
folder_row.Orientation = Orientation.Horizontal
folder_lbl = TextBlock()
folder_lbl.Text = fc.load_catalog_root() or u"(не задана)"
folder_lbl.Width = 470
folder_lbl.TextWrapping = TextWrapping.Wrap
folder_lbl.Foreground = Brushes.DimGray
pick_btn = Button()
pick_btn.Content = u"Выбрать…"
pick_btn.Padding = Thickness(10, 3, 10, 3)
pick_btn.Margin = Thickness(8, 0, 0, 0)


def on_pick(sender, args):
    new = fc.pick_catalog_root(fc.load_catalog_root())
    folder_lbl.Text = new or u"(не задана)"


pick_btn.Click += on_pick
folder_row.Children.Add(folder_lbl)
folder_row.Children.Add(pick_btn)
root_panel.Children.Add(folder_row)

# --- мониторинг при открытии ---
monitor_cb = CheckBox()
monitor_cb.Content = (
    u"Тихая проверка актуальности при открытии проекта (уведомление, если "
    u"есть устаревшие; каталог не сканируется)"
)
monitor_cb.IsChecked = fc.load_monitor_enabled()
monitor_cb.Margin = Thickness(0, 16, 0, 0)
monitor_cb.TextWrapping = TextWrapping.Wrap
root_panel.Children.Add(monitor_cb)

# --- авто-метка при штатной загрузке ---
autostamp_cb = CheckBox()
autostamp_cb.Content = (
    u"Ставить метку даты при штатной загрузке семейства из каталога "
    u"(меню Revit «Загрузить семейство»)"
)
autostamp_cb.IsChecked = fc.load_autostamp_enabled()
autostamp_cb.Margin = Thickness(0, 10, 0, 0)
autostamp_cb.TextWrapping = TextWrapping.Wrap
root_panel.Children.Add(autostamp_cb)

# --- видимый параметр для даты ---
root_panel.Children.Add(_row_label(u"Видимый параметр типа для даты (необязательно)"))
hint = TextBlock()
hint.Text = (
    u"Если задать имя текстового параметра типа — дата каталога дублируется "
    u"в него на всех типоразмерах, где он есть (скрытая метка пишется всегда)."
)
hint.FontSize = 11
hint.Foreground = Brushes.Gray
hint.TextWrapping = TextWrapping.Wrap
hint.Margin = Thickness(0, 0, 0, 2)
root_panel.Children.Add(hint)
param_box = TextBox()
param_box.Text = fc.load_stamp_param_name()
param_box.Padding = Thickness(4)
root_panel.Children.Add(param_box)

# --- кнопки ---
buttons = StackPanel()
buttons.Orientation = Orientation.Horizontal
buttons.HorizontalAlignment = HorizontalAlignment.Right
buttons.Margin = Thickness(16, 8, 16, 12)
DockPanel.SetDock(buttons, Dock.Bottom)

cancel_btn = Button()
cancel_btn.Content = u"Отмена"
cancel_btn.Padding = Thickness(12, 4, 12, 4)
cancel_btn.Margin = Thickness(0, 0, 8, 0)
ok_btn = Button()
ok_btn.Content = u"Сохранить"
ok_btn.Padding = Thickness(12, 4, 12, 4)
ok_btn.FontWeight = FontWeights.Bold

state = {"ok": False}


def on_ok(sender, args):
    state["ok"] = True
    win.Close()


def on_cancel(sender, args):
    win.Close()


ok_btn.Click += on_ok
cancel_btn.Click += on_cancel
buttons.Children.Add(cancel_btn)
buttons.Children.Add(ok_btn)

outer.Children.Add(buttons)
outer.Children.Add(root_panel)
win.Content = outer
win.ShowDialog()

if state["ok"]:
    fc.save_monitor_enabled(bool(monitor_cb.IsChecked))
    fc.save_autostamp_enabled(bool(autostamp_cb.IsChecked))
    fc.save_stamp_param_name(param_box.Text.strip())
    forms.alert(u"Настройки сохранены.", title=u"Семейства из каталога")
