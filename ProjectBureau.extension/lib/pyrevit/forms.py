# -*- coding: utf-8 -*-
"""
Заглушка pyrevit.forms — только то подмножество, что реально используется
в script.py / lib/pbtools этого репозитория: alert, ask_for_string,
pick_file, pick_folder, save_file, SelectFromList, ProgressBar.

Построено на System.Windows.Forms (не WPF) — этого достаточно для простых
модальных диалогов, а сами скрипты, которым нужны более сложные окна,
строят их напрямую через System.Windows.* (это уже есть в script.py/
pbtools и здесь не меняется).
"""
import clr
clr.AddReference("System.Windows.Forms")
clr.AddReference("System.Drawing")

from System.Windows.Forms import (
    MessageBox, MessageBoxButtons, MessageBoxIcon, DialogResult,
    OpenFileDialog, SaveFileDialog, FolderBrowserDialog,
    Form, FormStartPosition, FormBorderStyle,
    Label, TextBox, Button, ListBox, CheckedListBox,
    SelectionMode, DockStyle,
)
from System.Windows.Forms import ProgressBar as _WinProgressBar
from System.Drawing import Point, Size

from .script import ScriptExitException

APP_TITLE = u"ProjectBureau"


def alert(msg, title=None, ok=True, yes=False, no=False, exitscript=False,
          options=None, **_ignored):
    title = title or APP_TITLE

    if options:
        picked = _show_options_alert(msg, title, options)
        if exitscript and picked is None:
            raise ScriptExitException()
        return picked

    if yes or no:
        buttons = MessageBoxButtons.YesNo
    else:
        buttons = MessageBoxButtons.OK
    result = MessageBox.Show(msg, title, buttons, MessageBoxIcon.Information)
    ret = (result == DialogResult.Yes) if (yes or no) else None
    if exitscript:
        raise ScriptExitException()
    return ret


def _show_options_alert(msg, title, options):
    """forms.alert(msg, options=[...]) — окно с текстом и одной кнопкой на
    каждый вариант. Возвращает текст нажатой кнопки или None, если окно
    закрыли без выбора."""
    form = Form()
    form.Text = title
    form.FormBorderStyle = FormBorderStyle.FixedDialog
    form.StartPosition = FormStartPosition.CenterScreen
    form.MinimizeBox = False
    form.MaximizeBox = False

    label = Label()
    label.Text = msg
    label.Location = Point(12, 12)
    label.Size = Size(360, 60)
    form.Controls.Add(label)

    picked = {"value": None}

    def make_handler(value):
        def handler(sender, args):
            picked["value"] = value
            form.Close()
        return handler

    y = 84
    for opt in options:
        btn = Button()
        btn.Text = opt
        btn.Location = Point(12, y)
        btn.Size = Size(360, 28)
        btn.Click += make_handler(opt)
        form.Controls.Add(btn)
        y += 36

    form.ClientSize = Size(384, y + 12)
    form.ShowDialog()
    return picked["value"]


def ask_for_string(default=u"", prompt=u"", title=None):
    title = title or APP_TITLE
    form = Form()
    form.Text = title
    form.FormBorderStyle = FormBorderStyle.FixedDialog
    form.StartPosition = FormStartPosition.CenterScreen
    form.MinimizeBox = False
    form.MaximizeBox = False
    form.ClientSize = Size(420, 130)

    label = Label()
    label.Text = prompt
    label.Location = Point(12, 12)
    label.Size = Size(396, 40)

    box = TextBox()
    box.Text = default or u""
    box.Location = Point(12, 55)
    box.Size = Size(396, 24)

    ok_btn = Button()
    ok_btn.Text = u"ОК"
    ok_btn.DialogResult = DialogResult.OK
    ok_btn.Location = Point(252, 90)
    ok_btn.Size = Size(75, 28)

    cancel_btn = Button()
    cancel_btn.Text = u"Отмена"
    cancel_btn.DialogResult = DialogResult.Cancel
    cancel_btn.Location = Point(333, 90)
    cancel_btn.Size = Size(75, 28)

    form.Controls.Add(label)
    form.Controls.Add(box)
    form.Controls.Add(ok_btn)
    form.Controls.Add(cancel_btn)
    form.AcceptButton = ok_btn
    form.CancelButton = cancel_btn

    result = form.ShowDialog()
    if result != DialogResult.OK:
        return None
    return box.Text


def pick_file(file_ext="xlsx", title=None, **_ignored):
    dlg = OpenFileDialog()
    dlg.Title = title or u"Выберите файл"
    dlg.Filter = u"{0} files (*.{0})|*.{0}|All files (*.*)|*.*".format(file_ext)
    if dlg.ShowDialog() != DialogResult.OK:
        return None
    return dlg.FileName


def save_file(file_ext="xlsx", default_name=None, title=None, **_ignored):
    dlg = SaveFileDialog()
    dlg.Title = title or u"Сохранить как"
    dlg.Filter = u"{0} files (*.{0})|*.{0}|All files (*.*)|*.*".format(file_ext)
    dlg.DefaultExt = file_ext
    if default_name:
        dlg.FileName = default_name
    if dlg.ShowDialog() != DialogResult.OK:
        return None
    return dlg.FileName


def pick_folder(title=None, **_ignored):
    dlg = FolderBrowserDialog()
    if title:
        dlg.Description = title
    if dlg.ShowDialog() != DialogResult.OK:
        return None
    return dlg.SelectedPath


class SelectFromList(object):
    """forms.SelectFromList.show(options, title=, button_name=, multiselect=)

    options — список либо строк, либо произвольных объектов с осмысленным
    __str__ (как это принято у pyRevit). Список фильтруется по вводу в
    строке поиска (подстрока, без учёта регистра).
    """

    @classmethod
    def show(cls, options, title=None, button_name=u"OK", multiselect=False,
              **_ignored):
        options = list(options)
        title = title or APP_TITLE

        form = Form()
        form.Text = title
        form.StartPosition = FormStartPosition.CenterScreen
        form.MinimizeBox = False
        form.ClientSize = Size(460, 420)

        search = TextBox()
        search.Dock = DockStyle.Top

        if multiselect:
            listbox = CheckedListBox()
            listbox.CheckOnClick = True
        else:
            listbox = ListBox()
            listbox.SelectionMode = SelectionMode.One
        listbox.Dock = DockStyle.Fill
        listbox.IntegralHeight = False

        checked_state = {}  # id(option) -> bool, только для multiselect

        def visible_items(query):
            q = (query or u"").strip().lower()
            if not q:
                return options
            return [o for o in options if q in str(o).lower()]

        def refresh(query=u""):
            listbox.Items.Clear()
            for o in visible_items(query):
                if multiselect:
                    idx = listbox.Items.Add(o)
                    listbox.SetItemChecked(idx, checked_state.get(id(o), False))
                else:
                    listbox.Items.Add(o)

        def on_search_changed(sender, args):
            refresh(search.Text)

        search.TextChanged += on_search_changed

        if multiselect:
            def on_item_check(sender, args):
                from System.Windows.Forms import CheckState
                item = listbox.Items[args.Index]
                checked_state[id(item)] = (args.NewValue == CheckState.Checked)
            listbox.ItemCheck += on_item_check

        ok_btn = Button()
        ok_btn.Text = button_name or u"OK"
        ok_btn.DialogResult = DialogResult.OK
        ok_btn.Dock = DockStyle.Bottom
        cancel_btn = Button()
        cancel_btn.Text = u"Отмена"
        cancel_btn.DialogResult = DialogResult.Cancel
        cancel_btn.Dock = DockStyle.Bottom

        # Fill добавляется первым, затем Bottom/Top — иначе они не влезут
        # (WinForms докует в обратном порядке добавления).
        form.Controls.Add(listbox)
        form.Controls.Add(cancel_btn)
        form.Controls.Add(ok_btn)
        form.Controls.Add(search)
        form.AcceptButton = ok_btn
        form.CancelButton = cancel_btn

        refresh()

        result = form.ShowDialog()
        if result != DialogResult.OK:
            return [] if multiselect else None

        if multiselect:
            chosen = [o for o in options if checked_state.get(id(o), False)]
            return chosen
        else:
            if listbox.SelectedItem is None:
                return None
            return listbox.SelectedItem


class ProgressBar(object):
    """with forms.ProgressBar(title=u"...({value}/{max_value})") as pb:
           pb.update_progress(i, total)
    """

    def __init__(self, title=u"Выполняется… ({value}/{max_value})", **_ignored):
        self._title_tpl = title
        self._form = None
        self._bar = None

    def __enter__(self):
        self._form = Form()
        self._form.Text = APP_TITLE
        self._form.FormBorderStyle = FormBorderStyle.FixedDialog
        self._form.StartPosition = FormStartPosition.CenterScreen
        self._form.MinimizeBox = False
        self._form.MaximizeBox = False
        self._form.ControlBox = False
        self._form.ClientSize = Size(360, 70)

        self._bar = _WinProgressBar()
        self._bar.Location = Point(12, 12)
        self._bar.Size = Size(336, 24)
        self._bar.Minimum = 0
        self._bar.Maximum = 100
        self._form.Controls.Add(self._bar)

        self._label = Label()
        self._label.Location = Point(12, 42)
        self._label.Size = Size(336, 20)
        self._form.Controls.Add(self._label)

        self._form.Show()
        self._pump()
        return self

    def update_progress(self, value, max_value):
        try:
            self._bar.Maximum = max(int(max_value), 1)
            self._bar.Value = max(0, min(int(value), self._bar.Maximum))
            self._label.Text = self._title_tpl.format(value=value, max_value=max_value)
        except Exception:
            pass
        self._pump()

    def _pump(self):
        from System.Windows.Forms import Application
        Application.DoEvents()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._form is not None:
            self._form.Close()
        return False
