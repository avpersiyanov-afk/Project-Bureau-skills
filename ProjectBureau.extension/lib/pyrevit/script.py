# -*- coding: utf-8 -*-
"""
Заглушка pyrevit.script. Используется только get_output().print_md(),
exit() и clipboard_copy() — весь остальной pyrevit.script здесь не нужен.
"""
import clr
clr.AddReference("System.Windows.Forms")
from System.Windows.Forms import (
    Clipboard, Form, RichTextBox, FormStartPosition, DockStyle,
)


class ScriptExitException(Exception):
    """Ловится ProjectBureau.Loader.PythonHost — штатное завершение
    скрипта (аналог pyrevit.script.exit()), не показывается как ошибка."""
    pass


def exit():
    raise ScriptExitException()


def clipboard_copy(text):
    Clipboard.SetText(text if text else u"")


class _OutputWindow(object):
    """Упрощённая замена HTML-панели вывода pyRevit — обычное окно с
    моноширинным текстом. print_md() не рендерит markdown, а просто
    показывает текст как есть (сознательное упрощение)."""

    def __init__(self, title=u"Вывод"):
        self._form = Form()
        self._form.Text = title
        self._form.Width = 700
        self._form.Height = 500
        self._form.StartPosition = FormStartPosition.CenterScreen
        self._box = RichTextBox()
        self._box.Dock = DockStyle.Fill
        self._box.ReadOnly = True
        self._box.Font = _mono_font()
        self._form.Controls.Add(self._box)
        self._shown = False

    def print_md(self, text):
        self._box.AppendText(text + u"\r\n")
        if not self._shown:
            self._form.Show()
            self._shown = True
        else:
            self._form.BringToFront()


def _mono_font():
    from System.Drawing import Font, FontFamily
    return Font(FontFamily.GenericMonospace, 9.0)


_last_output = None


def get_output():
    global _last_output
    _last_output = _OutputWindow()
    return _last_output
