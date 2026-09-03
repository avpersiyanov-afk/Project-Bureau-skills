# -*- coding: utf-8 -*-
"""
Окно настроек параметров для кнопки «Помещение из связи» + их хранение
между запусками.

Хранится в JSON-файле %APPDATA%\\pyRevit\\LowLifeRoomInfo_settings.json —
тот же подход, что scs_settings.py/skud_settings.py (простой файл вместо
pyrevit.script.get_config(), см. их докстринги про причину).

Значения — имена параметров, специфичные для конкретного проекта/ФОП,
поэтому никогда не задаются константами в коде, только через это окно.
"""

import os
import io
import json

import clr
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')

from pyrevit import forms

from pbtools import settings_transfer

from System.Windows import (
    Window, WindowStartupLocation, Thickness,
    FontWeights, HorizontalAlignment, TextWrapping
)
from System.Windows.Controls import (
    StackPanel, TextBlock, TextBox, Button, Orientation, DockPanel, Dock
)
from System.Windows.Media import Brushes

SETTINGS_FILE_NAME = "LowLifeRoomInfo_settings.json"

# (ключ, заголовок раздела, подпись поля, пояснение под полем,
#  значение по умолчанию, обязательное ли поле)
#
# Два поля легко перепутать, т.к. оба про «параметр помещения» — но
# относятся к РАЗНЫМ документам: первое пишет в текущую модель, второе
# читает из модели-связи. Поэтому в окне разнесены по разделам с ①/②,
# а не просто друг под другом.
TEXT_FIELDS = [
    (
        "target_param_name",
        u"① Куда записывать — в этой модели",
        u"Параметр элемента",
        u"Сюда запишется «Имя (Номер)». Например «SMNX_Помещение».",
        u"", True
    ),
    (
        "room_number_param_name",
        u"② Откуда брать номер — в связанной модели",
        u"Параметр помещения (Room) в связи",
        u"Общий параметр Room в файле связи, физически хранящий номер. "
        u"Например «SMNX_Номер помещения».",
        u"", True
    ),
]

PLAIN_LABELS = {key: label for key, _section, label, _hint, _default, _required in TEXT_FIELDS}


def _settings_file_path():
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    folder = os.path.join(appdata, "pyRevit")

    if not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except:
            pass

    return os.path.join(folder, SETTINGS_FILE_NAME)


def _read_all():
    path = _settings_file_path()

    if not os.path.isfile(path):
        return {}

    try:
        with io.open(path, "r", encoding="utf-8") as f:
            text = f.read()
        if not text.strip():
            return {}
        return json.loads(text)
    except:
        return {}


def _write_all(data):
    path = _settings_file_path()

    try:
        with io.open(path, "w", encoding="utf-8") as f:
            f.write(unicode(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)))
    except:
        forms.alert(
            u"Не удалось сохранить настройки в файл:\n{}".format(path)
        )


def load_saved_values():
    """Строковые значения настроек: из JSON-файла, иначе — значения по умолчанию."""
    saved = _read_all()
    return {key: saved.get(key, default) for key, _section, _label, _hint, default, _required in TEXT_FIELDS}


def save_values(values):
    data = _read_all()
    data.update(values)
    _write_all(data)


def require(settings, keys):
    """
    Проверяет, что перечисленные ключи заполнены. Останавливает скрипт
    через forms.alert(exitscript=True), если чего-то не хватает.
    """
    missing = []

    for key in keys:
        value = settings.get(key)
        if not (value and unicode(value).strip()):
            missing.append(PLAIN_LABELS.get(key, key))

    if missing:
        forms.alert(
            u"Не заполнены обязательные настройки:\n\n{}\n\n"
            u"Запустите кнопку «Параметры помещений» и заполните их там.".format(
                u"\n".join(missing)
            ),
            exitscript=True
        )


def show_settings_form(values):
    """Модальное окно редактирования настроек. Возвращает словарь строковых
    значений или None, если пользователь отменил."""
    result = {"values": None}

    win = Window()
    win.Title = u"Настройки: Помещение из связи"
    win.Width = 780
    win.Height = 400
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen

    outer = DockPanel()
    outer.LastChildFill = True

    root = StackPanel()
    root.Margin = Thickness(16)

    title = TextBlock()
    title.Text = u"Параметры для переноса помещения из связи"
    title.FontSize = 16
    title.FontWeight = FontWeights.Bold
    title.Margin = Thickness(0, 0, 0, 4)
    root.Children.Add(title)

    hint = TextBlock()
    hint.Text = u"Значения сохраняются и подставляются при следующих запусках."
    hint.FontSize = 11
    hint.Foreground = Brushes.Gray
    hint.Margin = Thickness(0, 0, 0, 10)
    root.Children.Add(hint)

    boxes = {}

    for key, section_title, label_text, hint_text, _, required in TEXT_FIELDS:
        section = TextBlock()
        section.Text = section_title
        section.FontWeight = FontWeights.Bold
        section.Margin = Thickness(0, 16, 0, 2)
        root.Children.Add(section)

        label = TextBlock()
        label.Text = label_text + (u" *" if required else u"")
        label.Margin = Thickness(0, 2, 0, 2)
        label.TextWrapping = TextWrapping.Wrap
        root.Children.Add(label)

        box = TextBox()
        box.Text = values.get(key, "")
        box.Padding = Thickness(4)
        root.Children.Add(box)
        boxes[key] = box

        hint = TextBlock()
        hint.Text = hint_text
        hint.FontSize = 11
        hint.Foreground = Brushes.Gray
        hint.TextWrapping = TextWrapping.Wrap
        hint.Margin = Thickness(0, 2, 0, 0)
        root.Children.Add(hint)

    required_hint = TextBlock()
    required_hint.Text = u"* обязательные поля"
    required_hint.FontSize = 11
    required_hint.Foreground = Brushes.Gray
    required_hint.Margin = Thickness(0, 10, 0, 0)
    root.Children.Add(required_hint)

    buttons = StackPanel()
    buttons.Orientation = Orientation.Horizontal
    buttons.HorizontalAlignment = HorizontalAlignment.Right
    buttons.Margin = Thickness(16, 8, 16, 12)
    DockPanel.SetDock(buttons, Dock.Bottom)

    cancel_btn = Button()
    cancel_btn.Content = u"Отмена"
    cancel_btn.Padding = Thickness(10, 4, 10, 4)
    cancel_btn.Margin = Thickness(0, 0, 8, 0)

    ok_btn = Button()
    ok_btn.Content = u"Сохранить"
    ok_btn.Padding = Thickness(10, 4, 10, 4)
    ok_btn.FontWeight = FontWeights.Bold

    def on_ok(sender, args):
        result["values"] = {key: box.Text for key, box in boxes.items()}
        win.Close()

    def on_cancel(sender, args):
        win.Close()

    ok_btn.Click += on_ok
    cancel_btn.Click += on_cancel

    buttons.Children.Add(cancel_btn)
    buttons.Children.Add(ok_btn)

    def _on_settings_imported():
        result["values"] = settings_transfer.RELOAD
        win.Close()

    settings_transfer.add_transfer_buttons(
        buttons, _read_all, _write_all, u"помещений", _on_settings_imported
    )

    outer.Children.Add(buttons)
    outer.Children.Add(root)

    win.Content = outer
    win.ShowDialog()

    return result["values"]


def get_settings_interactive():
    """
    Показывает окно настроек, сохраняет введённые значения и возвращает
    их. Возвращает None, если пользователь нажал «Отмена». Используется
    только кнопкой «Параметры помещений».
    """
    while True:
        saved = load_saved_values()
        edited = show_settings_form(saved)

        if edited == settings_transfer.RELOAD:
            # настройки загружены из файла и уже записаны — открываем заново
            continue

        if edited is None:
            return None

        save_values(edited)
        return edited


def get_settings_silent():
    """
    Настройки без показа окна — уже сохранённые значения (или пустые
    строки, если ещё ничего не настроено). Используется кнопкой
    «Помещение из связи».
    """
    return load_saved_values()
