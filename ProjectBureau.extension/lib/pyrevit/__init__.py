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

__all__ = ["revit", "forms", "script", "DB", "EXEC_PARAMS", "interop_singleton"]

# Кэш CLR-типов, порождённых классами, что реализуют .NET-интерфейс напрямую
# (__namespace__ = "..." — см. family_catalog.OverwriteFamilyLoadOptions,
# selection.ModelElementSelectionFilter). Такой class-оператор через
# pythonnet один раз создаёт настоящий .NET-тип в Reflection.Emit-сборке;
# повторное выполнение того же class-оператора (после «Обновить с GitHub» —
# ProjectBureau.Loader.PythonHost.InvalidateModuleCache сбрасывает
# pyrevit.*/pbtools.* из sys.modules и вызывает свежий импорт) падает
# "TypeError: Повторяющееся имя типа в пределах сборки", т.к. Reflection.Emit
# не позволяет второй тип с тем же полным именем в одном процессе.
# Кладём кэш в builtins, а не в globals() этого модуля — сам pyrevit тоже
# перевыполняется с нуля при повторном импорте, обычный module-level словарь
# не пережил бы сброс sys.modules.
if not hasattr(builtins, "_pb_interop_cache"):
    builtins._pb_interop_cache = {}


def interop_singleton(key, builder):
    """builder() один раз строит и возвращает класс, реализующий .NET-
    интерфейс; при повторном импорте модуля переиспользуется уже созданный
    CLR-тип вместо падения на дубликате. Правки внутри такого класса
    вступают в силу только после перезапуска Revit — самообновление с
    GitHub на них не распространяется."""
    cache = builtins._pb_interop_cache
    if key not in cache:
        cache[key] = builder()
    return cache[key]
