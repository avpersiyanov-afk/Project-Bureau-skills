# -*- coding: utf-8 -*-
"""
Заглушка pyrevit.revit. doc/uidoc выставляются ProjectBureau.Loader
(PythonHost.RunScript) перед exec() каждого script.py — на момент import
этого модуля их значения ещё не важны.
"""
import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit.DB import Transaction as _Transaction

doc = None
uidoc = None


class _Selection(object):
    """Минимальная замена pyrevit.revit.Selection — используется только
    .elements (и прямая итерация, на случай list(selection))."""

    def __init__(self, elements):
        self.elements = elements

    def __iter__(self):
        return iter(self.elements)

    def __len__(self):
        return len(self.elements)


def get_selection():
    ids = uidoc.Selection.GetElementIds()
    elements = [doc.GetElement(i) for i in ids]
    return _Selection(elements)


class Transaction(object):
    """with revit.Transaction(u"Имя"): ..."""

    def __init__(self, name):
        self.name = name
        self._t = None

    def __enter__(self):
        self._t = _Transaction(doc, self.name)
        self._t.Start()
        return self._t

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self._t.Commit()
        else:
            self._t.RollBack()
        return False
