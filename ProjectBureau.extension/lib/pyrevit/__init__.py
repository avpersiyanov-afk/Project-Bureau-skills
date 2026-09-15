# -*- coding: utf-8 -*-
"""
Шим настоящего pyrevit.* — совместимый по API набор заглушек, нужный только
для того, чтобы существующие script.py и lib/pbtools/*.py (написанные под
pyRevit) выполнялись без единой правки под собственным загрузчиком
ProjectBureau.Loader, без установленного pyRevit.

Реализует только то подмножество pyrevit.forms / revit / script / DB /
EXEC_PARAMS, которое реально используется в этом репозитории — не полная
переработка pyRevit.

ProjectBureau.Loader кладёт папку lib/pyrevit первой в sys.path, поэтому
`from pyrevit import ...` резолвится сюда, а не в настоящий pyRevit (если он
вообще установлен на машине).
"""
import builtins

# Многие скрипты писались ещё под IronPython 2.7 и используют unicode()/
# basestring — настоящий движок pyRevit (CPython) добавляет эти имена в
# builtins для обратной совместимости. Делаем то же самое.
builtins.unicode = str
builtins.basestring = str

import clr
clr.AddReference("RevitAPI")
from Autodesk.Revit import DB  # noqa: E402  (нужен clr.AddReference выше)


class _ExecParams(object):
    """Заполняется ProjectBureau.Loader перед exec() каждого script.py."""

    def __init__(self):
        self.config_mode = False


EXEC_PARAMS = _ExecParams()

from . import revit  # noqa: E402,F401
from . import forms  # noqa: E402,F401
from . import script  # noqa: E402,F401

__all__ = ["revit", "forms", "script", "DB", "EXEC_PARAMS"]
