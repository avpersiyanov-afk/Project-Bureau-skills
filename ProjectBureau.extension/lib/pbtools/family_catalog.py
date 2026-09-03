# -*- coding: utf-8 -*-
"""
Обновление загружаемых семейств выбранной категории из папки-каталога
.rfa: подбор файла по похожему имени + перезагрузка семейства с заменой
значений параметров (overwrite parameter values).

Что делает кнопка «Обновить семейства» (Tools.panel):
1. запоминает путь к корневой папке каталога в
   %APPDATA%\\pyRevit\\LowLifeFamilyCatalog_settings.json (тот же подход,
   что у scs_settings.py — простой JSON, а не pyrevit.script.get_config());
2. рекурсивно собирает все .rfa из каталога (подпапки тоже), пропуская
   резервные копии Revit вида «Имя.0001.rfa» и папки с «архив» в имени
   (EXCLUDED_DIR_KEYWORDS);
3. по выбранной пользователем категории берёт загруженные в проект
   семейства и для каждого ищет в каталоге файл с самым похожим именем
   (коэффициент Сёренсена—Дайса по буквенным биграммам нормализованных
   имён — без внешних зависимостей);
4. показывает таблицу «семейство → файл каталога (N%)» с галочками, где
   можно вручную сменить файл;
5. для отмеченных строк копирует .rfa во временный файл, ПЕРЕИМЕНОВАННЫЙ
   в имя семейства модели (Revit сопоставляет семейство при загрузке по
   имени файла), и вызывает Document.LoadFamily с IFamilyLoadOptions,
   возвращающим overwriteParameterValues = True — так значения параметров
   типов берутся из файла каталога;
6. если в окне включён флажок «переименовывать» и имя файла каталога
   отличается от имени семейства модели — после загрузки семейство модели
   переименовывается в имя файла каталога (rename_family, в транзакции).
   Это и есть сценарий «в каталоге семейство переименовали»: LoadFamily
   при совпадающем содержимом вернёт False (status="unchanged", НЕ ошибка),
   а нужное изменение — само переименование.

Document.LoadFamily управляет собственной транзакцией и не должен
вызываться внутри открытой транзакции, поэтому кнопка не оборачивает
цикл загрузки в revit.Transaction — каждая перезагрузка семейства
является отдельным шагом отмены.

После успешной загрузки в сам элемент Family пишется скрытая метка
(ExtensibleStorage, схема SCHEMA_GUID) с датой изменения файла .rfa в
каталоге. Кнопка «Актуальность семейств» потом сравнивает эту метку с
текущей датой файла и показывает, какие семейства устарели. Запись метки
идёт SetEntity — это уже требует открытой транзакции, поэтому пометка
выполняется отдельным проходом ПОСЛЕ всех LoadFamily, внутри
revit.Transaction (см. write_stamp).
"""

import os
import io
import json
import shutil
import tempfile
import datetime

import clr
clr.AddReference('RevitAPI')
clr.AddReference('System')
clr.AddReference('PresentationFramework')
clr.AddReference('PresentationCore')
clr.AddReference('WindowsBase')

from Autodesk.Revit.DB import (
    FilteredElementCollector, Family, Element, ElementId,
    IFamilyLoadOptions, FamilySource, Transaction, StorageType
)
from Autodesk.Revit.DB.ExtensibleStorage import (
    Schema, SchemaBuilder, AccessLevel, Entity
)

from System import Guid, String
from System.Collections.Generic import List

from pyrevit import forms

from System.Windows import (
    Window, WindowStartupLocation, Thickness,
    FontWeights, HorizontalAlignment, VerticalAlignment, TextWrapping
)
from System.Windows.Controls import (
    StackPanel, TextBlock, Button, CheckBox, Orientation, DockPanel, Dock,
    ScrollViewer, ScrollBarVisibility,
    DataGrid, DataGridCheckBoxColumn, DataGridTextColumn,
    DataGridLength, DataGridLengthUnitType,
    DataGridHeadersVisibility, DataGridGridLinesVisibility, DataGridSelectionMode
)
from System.Windows.Data import Binding, BindingMode, UpdateSourceTrigger
from System.Windows.Media import Brushes
from System.ComponentModel import ListSortDirection


SETTINGS_FILE_NAME = "LowLifeFamilyCatalog_settings.json"
CATALOG_ROOT_KEY = "catalog_root"

# Порог, при котором строка предпросмотра включается галочкой сразу.
AUTO_CHECK_SCORE = 0.72

# Ниже этой похожести имя-в-каталоге считается «не тем файлом»: подсказка
# в таблице остаётся, но статус актуальности = «нет в каталоге» (иначе
# дату метки сравнивали бы со случайным непохожим файлом).
MATCH_FLOOR = 0.45

# Недопустимые в имени файла символы (заменяем на "_" во временном .rfa).
_BAD_FILENAME_CHARS = u'\\/:*?"<>|'

# Схема скрытой метки на элементе Family (ExtensibleStorage). GUID
# фиксированный — менять нельзя, иначе старые метки перестанут читаться.
SCHEMA_GUID = Guid("d7b1e6a2-4c3f-4b9a-9e21-6f8c1a2b3c4d")
SCHEMA_NAME = "LowLifeFamilyCatalogStamp"
SCHEMA_VENDOR = "LOWLIFE"

_F_MTIME_EPOCH = "catalog_mtime_epoch"   # строка float-секунд (для сравнения)
_F_MTIME_ISO = "catalog_mtime_iso"       # "ГГГГ-ММ-ДД ЧЧ:ММ" (для человека)
_F_CATALOG_FILE = "catalog_file"         # относительный путь файла каталога
_F_UPDATED_AT = "updated_at_iso"         # когда кнопка проставила метку

# Насколько новее должен быть файл каталога, чтобы считать семейство
# устаревшим (сек) — запас против расхождения часов/округления mtime.
STALE_TOLERANCE_SEC = 90

# Статусы актуальности (assess_families / MatchRow.status).
STATUS_STALE = u"устарело"
STATUS_NO_STAMP = u"нет метки"
STATUS_CURRENT = u"актуально"
STATUS_NO_CATALOG = u"нет в каталоге"

# Сигнал «вернуться на предыдущий шаг» из окна выбора типоразмеров.
BACK = u"__back__"


# --------------------------------------------------------------------------
# Хранение пути к каталогу
# --------------------------------------------------------------------------

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
        forms.alert(u"Не удалось сохранить настройки каталога семейств:\n{}".format(path))


def load_catalog_root():
    """Сохранённый путь к корневой папке каталога либо пустая строка."""
    return _read_all().get(CATALOG_ROOT_KEY, u"") or u""


def save_catalog_root(path):
    data = _read_all()
    data[CATALOG_ROOT_KEY] = unicode(path or u"")
    _write_all(data)


MONITOR_KEY = "monitor_on_open"
AUTOSTAMP_KEY = "autostamp_on_load"
STAMP_PARAM_KEY = "stamp_param_name"


def load_monitor_enabled():
    """Проверять актуальность семейств при открытии проекта (тихо, toast)?"""
    return bool(_read_all().get(MONITOR_KEY, False))


def save_monitor_enabled(value):
    data = _read_all()
    data[MONITOR_KEY] = bool(value)
    _write_all(data)


def load_autostamp_enabled():
    """Ставить метку даты при штатной загрузке семейства из каталога (хук)?"""
    return bool(_read_all().get(AUTOSTAMP_KEY, False))


def save_autostamp_enabled(value):
    data = _read_all()
    data[AUTOSTAMP_KEY] = bool(value)
    _write_all(data)


def load_stamp_param_name():
    """Имя видимого текстового параметра типа, куда дублировать дату (или '')."""
    return _read_all().get(STAMP_PARAM_KEY, u"") or u""


def save_stamp_param_name(name):
    data = _read_all()
    data[STAMP_PARAM_KEY] = unicode(name or u"")
    _write_all(data)


def stamp_loaded_family(doc, family, family_path):
    """
    Для хука family-loaded: если family_path лежит ВНУТРИ настроенного
    каталога — пишет скрытую метку (и видимый параметр, если задан) с
    датой этого файла. Своя транзакция, всё под try. Возвращает True,
    если пометил.
    """
    if family is None or not family_path:
        return False
    root = load_catalog_root()
    if not root or not os.path.isdir(root):
        return False
    try:
        full = os.path.normcase(os.path.abspath(family_path))
        rootn = os.path.normcase(os.path.abspath(root))
        if not (full == rootn or full.startswith(rootn + os.sep)):
            return False
        rel = os.path.relpath(family_path, root)
    except:
        return False

    epoch, iso = file_mtime(family_path)
    if epoch is None:
        return False

    pname = load_stamp_param_name()
    t = Transaction(doc, u"Метка даты каталога (авто)")
    try:
        t.Start()
        write_stamp(family, epoch, iso, rel)
        if pname:
            write_stamp_param(doc, family, iso, pname)
        t.Commit()
        return True
    except:
        try:
            if t.HasStarted() and not t.HasEnded():
                t.RollBack()
        except:
            pass
        return False


def write_stamp_param(doc, family, iso_text, param_name):
    """
    Дублирует дату каталога (строкой) в видимый текстовый параметр типа
    param_name на всех типоразмерах семейства, у которых он есть.
    **Требует открытой транзакции.** Тихо ничего не делает, если имя не
    задано или параметра нет. Возвращает число обновлённых типоразмеров.
    """
    if not param_name or family is None:
        return 0
    n = 0
    try:
        sym_ids = list(family.GetFamilySymbolIds())
    except:
        return 0
    for sid in sym_ids:
        sym = doc.GetElement(sid)
        if sym is None:
            continue
        try:
            p = sym.LookupParameter(param_name)
        except:
            p = None
        if p is None or p.IsReadOnly:
            continue
        try:
            if p.StorageType == StorageType.String:
                p.Set(iso_text or u"")
                n += 1
        except:
            pass
    return n


def pick_catalog_root(current=None):
    """
    Диалог выбора папки-каталога + сохранение. Возвращает новый путь; если
    пользователь отменил — прежний current (когда он валиден) либо None.
    """
    try:
        picked = forms.pick_folder(
            title=u"Папка-каталог семейств (.rfa, включая подпапки)"
        )
    except TypeError:
        picked = forms.pick_folder()

    if picked and os.path.isdir(picked):
        save_catalog_root(picked)
        return picked

    if current and os.path.isdir(current):
        return current
    return None


def resolve_catalog_root(force_pick=False):
    """
    Путь к каталогу для рабочих кнопок: сохранённый; если его нет/он битый
    или force_pick=True (Shift+клик / config.py) — спросить папку и сохранить.
    Возвращает путь либо None.
    """
    root = load_catalog_root()
    if force_pick or not root or not os.path.isdir(root):
        root = pick_catalog_root(root)
    return root


# --------------------------------------------------------------------------
# Имена: нормализация и похожесть
# --------------------------------------------------------------------------

def _safe_element_name(el):
    """
    Имя элемента через Element.Name.GetValue(el): прямой el.Name в
    IronPython у Family/FamilySymbol падает с ошибкой неоднозначного
    связывания и незаметно уходит в except (см. scs_settings._safe_element_name).
    """
    try:
        return Element.Name.GetValue(el)
    except:
        try:
            return el.Name
        except:
            return None


def _norm(s):
    """Нижний регистр, только буквы и цифры (убирает пробелы, «_», «-», «.» и т.п.)."""
    s = unicode(s or u"").lower().strip()
    return u"".join(ch for ch in s if ch.isalnum())


def _bigrams(s):
    if len(s) < 2:
        return set([s]) if s else set()
    return set(s[i:i + 2] for i in range(len(s) - 1))


def similarity(a, b):
    """
    Похожесть имён 0..1 — коэффициент Сёренсена—Дайса по буквенным
    биграммам нормализованных имён. Точное совпадение после нормализации
    даёт 1.0; если одно имя целиком входит в другое — не ниже 0.9.
    Своя реализация вместо difflib — чтобы не зависеть от полноты
    стандартной библиотеки движка pyRevit.
    """
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    ba, bb = _bigrams(na), _bigrams(nb)
    denom = len(ba) + len(bb)
    dice = (2.0 * len(ba & bb) / denom) if denom else 0.0

    if na in nb or nb in na:
        dice = max(dice, 0.9)

    return dice


# --------------------------------------------------------------------------
# Каталог .rfa
# --------------------------------------------------------------------------

# Папки, чьё имя содержит одно из этих слов (без учёта регистра), в обход
# каталога не заходят вместе со всем содержимым — например «Архив», «Архив 2023».
EXCLUDED_DIR_KEYWORDS = (u"архив", u"archive", u"archiv")


def _is_excluded_dir(name):
    low = unicode(name or u"").lower()
    return any(kw in low for kw in EXCLUDED_DIR_KEYWORDS)


def _is_backup_rfa(filename):
    """«Имя.0001.rfa» — резервная копия, создаваемая Revit; в каталог не берём."""
    low = filename.lower()
    if not low.endswith(".rfa"):
        return False
    stem = low[:-4]
    tail = stem.rsplit(".", 1)[-1] if "." in stem else u""
    return len(tail) == 4 and tail.isdigit()


def file_mtime(path):
    """(epoch_float, "ГГГГ-ММ-ДД ЧЧ:ММ") для файла либо (None, None)."""
    try:
        epoch = os.path.getmtime(path)
    except:
        return (None, None)
    try:
        iso = datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M")
    except:
        iso = u""
    return (epoch, iso)


class CatalogEntry(object):
    """Файл семейства из каталога."""

    def __init__(self, path, root):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        try:
            self.rel = os.path.relpath(path, root)
        except:
            self.rel = os.path.basename(path)
        self.mtime, self.mtime_iso = file_mtime(path)

    def __str__(self):
        return self.rel


def scan_catalog(root):
    """
    Все .rfa из root и его подпапок (без резервных копий Revit). Папки,
    чьё имя содержит «архив» (см. EXCLUDED_DIR_KEYWORDS), пропускаются
    целиком. Список CatalogEntry.
    """
    entries = []
    seen = set()

    for dirpath, dirnames, filenames in os.walk(root):
        # правим dirnames на месте — os.walk не зайдёт в отброшенные папки
        dirnames[:] = [d for d in dirnames if not _is_excluded_dir(d)]

        for fn in filenames:
            if not fn.lower().endswith(".rfa") or _is_backup_rfa(fn):
                continue
            full = os.path.join(dirpath, fn)
            key = os.path.normcase(os.path.abspath(full))
            if key in seen:
                continue
            seen.add(key)
            entries.append(CatalogEntry(full, root))

    entries.sort(key=lambda e: e.rel.lower())
    return entries


# --------------------------------------------------------------------------
# Скрытая метка даты каталога на элементе Family (ExtensibleStorage)
# --------------------------------------------------------------------------

def _get_or_create_schema():
    schema = Schema.Lookup(SCHEMA_GUID)
    if schema is not None:
        return schema

    sb = SchemaBuilder(SCHEMA_GUID)
    sb.SetSchemaName(SCHEMA_NAME)
    sb.SetVendorId(SCHEMA_VENDOR)
    sb.SetReadAccessLevel(AccessLevel.Public)
    sb.SetWriteAccessLevel(AccessLevel.Public)
    # Все поля строковые — так у SimpleField не требуется указывать
    # единицы/спецификацию (обязательно для double/int в Revit 2022+).
    sb.AddSimpleField(_F_MTIME_EPOCH, String)
    sb.AddSimpleField(_F_MTIME_ISO, String)
    sb.AddSimpleField(_F_CATALOG_FILE, String)
    sb.AddSimpleField(_F_UPDATED_AT, String)
    return sb.Finish()


def read_stamp(family):
    """
    dict со скрытой меткой семейства {epoch, iso, file, updated_at} либо
    None, если метки нет. epoch — float или None.
    """
    try:
        schema = Schema.Lookup(SCHEMA_GUID)
        if schema is None:
            return None
        ent = family.GetEntity(schema)
        if ent is None or not ent.IsValid():
            return None
        epoch_str = ent.Get[String](_F_MTIME_EPOCH)
        try:
            epoch = float(epoch_str) if epoch_str else None
        except:
            epoch = None
        return {
            "epoch": epoch,
            "iso": ent.Get[String](_F_MTIME_ISO),
            "file": ent.Get[String](_F_CATALOG_FILE),
            "updated_at": ent.Get[String](_F_UPDATED_AT),
        }
    except:
        return None


def write_stamp(family, mtime_epoch, mtime_iso, catalog_file):
    """
    Пишет скрытую метку даты каталога в элемент Family. **Требует открытой
    транзакции** (SetEntity). Возвращает True/False.
    """
    try:
        schema = _get_or_create_schema()
        ent = Entity(schema)
        ent.Set[String](_F_MTIME_EPOCH, u"{}".format(mtime_epoch if mtime_epoch is not None else u""))
        ent.Set[String](_F_MTIME_ISO, mtime_iso or u"")
        ent.Set[String](_F_CATALOG_FILE, catalog_file or u"")
        ent.Set[String](_F_UPDATED_AT, datetime.datetime.now().strftime("%Y-%m-%d %H:%M"))
        family.SetEntity(ent)
        return True
    except:
        return False


def stamp_status(stamp, entry):
    """
    Статус актуальности семейства по скрытой метке и текущему файлу
    каталога: STATUS_NO_CATALOG / STATUS_NO_STAMP / STATUS_STALE / STATUS_CURRENT.
    """
    if entry is None:
        return STATUS_NO_CATALOG
    if not stamp:
        return STATUS_NO_STAMP
    stamp_epoch = stamp.get("epoch")
    if stamp_epoch is None or entry.mtime is None:
        return STATUS_NO_STAMP
    if entry.mtime > stamp_epoch + STALE_TOLERANCE_SEC:
        return STATUS_STALE
    return STATUS_CURRENT


def check_stale_against_catalog(doc):
    """
    Быстрая тихая проверка актуальности: НЕ сканирует каталог. Для каждого
    семейства модели со скрытой меткой берёт путь файла и дату из самой
    метки (write_stamp пишет и относительный путь, и epoch), делает один
    os.path.getmtime по этому пути и сравнивает. Дёшево даже на сетевой
    библиотеке (по одному stat на помеченное семейство).

    Возвращает (stale, checked, names) — сколько устарело, сколько
    проверено и имена устаревших (до 20).
    """
    root = load_catalog_root()
    if not root or not os.path.isdir(root):
        return (0, 0, [])

    stale = 0
    checked = 0
    names = []
    for fam in FilteredElementCollector(doc).OfClass(Family):
        st = read_stamp(fam)
        if not st:
            continue
        epoch = st.get("epoch")
        rel = st.get("file")
        if epoch is None or not rel:
            continue
        try:
            mt = os.path.getmtime(os.path.join(root, rel))
        except:
            continue
        checked += 1
        if mt > epoch + STALE_TOLERANCE_SEC:
            stale += 1
            nm = _safe_element_name(fam)
            if nm and len(names) < 20:
                names.append(nm)
    return (stale, checked, names)


def find_family_by_name(doc, name):
    for fam in FilteredElementCollector(doc).OfClass(Family):
        if _safe_element_name(fam) == name:
            return fam
    return None


# --------------------------------------------------------------------------
# Семейства проекта
# --------------------------------------------------------------------------

def _iter_loadable_families(doc):
    for fam in FilteredElementCollector(doc).OfClass(Family):
        try:
            if fam.IsInPlace:
                continue
        except:
            pass
        try:
            cat = fam.FamilyCategory
        except:
            cat = None
        if cat is None:
            continue
        yield fam, cat


class CategoryOption(object):
    """Категория с числом загружаемых семейств — для forms.SelectFromList."""

    def __init__(self, cat, count):
        self.cat_id = cat.Id
        try:
            base = cat.Name
        except:
            base = _safe_element_name(cat)
        base = base or u"?"
        self.sort_name = base.lower()
        self.name = u"{} ({})".format(base, count)

    def __str__(self):
        return self.name


def list_family_categories(doc):
    """CategoryOption для каждой категории, где есть загружаемые (не in-place) семейства."""
    cats = {}
    counts = {}

    for fam, cat in _iter_loadable_families(doc):
        cid = cat.Id.IntegerValue
        cats[cid] = cat
        counts[cid] = counts.get(cid, 0) + 1

    options = [CategoryOption(cats[cid], counts[cid]) for cid in cats]
    options.sort(key=lambda o: o.sort_name)
    return options


def list_families_in_categories(doc, cat_ids):
    """
    Загружаемые семейства из перечисленных категорий. cat_ids — набор
    ElementId либо целых (IntegerValue).
    """
    targets = set()
    for cid in cat_ids:
        try:
            targets.add(cid.IntegerValue)
        except AttributeError:
            targets.add(int(cid))

    result = []
    for fam, cat in _iter_loadable_families(doc):
        if cat.Id.IntegerValue in targets:
            result.append(fam)

    result.sort(key=lambda f: (_safe_element_name(f) or u"").lower())
    return result


def list_families_in_category(doc, cat_id):
    """Загружаемые семейства одной категории (cat_id — ElementId)."""
    return list_families_in_categories(doc, [cat_id])


def project_family_names(doc):
    """Множество имён всех загружаемых семейств проекта (для «уже в модели?»)."""
    names = set()
    for fam in FilteredElementCollector(doc).OfClass(Family):
        nm = _safe_element_name(fam)
        if nm:
            names.add(nm)
    return names


class MatchRow(object):
    """Строка сопоставления: семейство модели и подобранный файл каталога."""

    def __init__(self, family, family_name, entry, score, stamp, status):
        self.family = family
        self.family_name = family_name
        self.entry = entry          # CatalogEntry или None
        self.score = score          # 0..1
        self.stamp = stamp          # dict скрытой метки или None
        self.status = status        # STATUS_* — актуальность по метке


# Порядок статусов для сортировки/отчёта: сперва то, что требует внимания.
_STATUS_ORDER = {
    STATUS_STALE: 0,
    STATUS_NO_STAMP: 1,
    STATUS_NO_CATALOG: 2,
    STATUS_CURRENT: 3,
}


def build_matches(families, entries):
    """
    Для каждого семейства — лучший по имени файл каталога + статус
    актуальности по скрытой метке. Сортировка: сперва устаревшие/без
    метки, затем по убыванию похожести и имени.
    """
    rows = []

    for fam in families:
        fam_name = _safe_element_name(fam) or u"?"
        best = None
        best_score = 0.0

        for e in entries:
            sc = similarity(fam_name, e.name)
            if sc > best_score:
                best_score = sc
                best = e

        stamp = read_stamp(fam)
        confident = best if best_score >= MATCH_FLOOR else None
        status = stamp_status(stamp, confident)
        rows.append(MatchRow(fam, fam_name, best, best_score, stamp, status))

    rows.sort(key=lambda r: (
        _STATUS_ORDER.get(r.status, 9), -r.score, r.family_name.lower()
    ))
    return rows


# --------------------------------------------------------------------------
# Перезагрузка семейства с заменой параметров
# --------------------------------------------------------------------------

class OverwriteFamilyLoadOptions(IFamilyLoadOptions):
    """
    IFamilyLoadOptions для перезагрузки семейства.

    overwrite_parameter_values управляет тем же, что выбор в диалоге Revit
    при загрузке семейства, уже присутствующего в проекте:
      True  — «Перезаписать существующую версию и значения параметров»:
              для типоразмеров, что есть и в проекте, и в файле, значения
              параметров берутся из файла каталога;
      False — «Перезаписать существующую версию»: определение семейства
              (геометрия, формулы, набор типоразмеров, параметры) всё равно
              обновляется, но существующие значения параметров у уже
              имеющихся типоразмеров сохраняются.
    В обоих случаях новые типоразмеры из файла добавляются, а типоразмеры,
    которых в файле нет, Revit из проекта не удаляет.

    Для общих (shared) вложенных семейств источник — сам загружаемый файл.
    """

    # ВАЖНО: у класса, реализующего .NET-интерфейс, под IronPython НЕ должно
    # быть собственного __init__ — иначе Revit не распознаёт реализацию и не
    # вызывает OnFamilyFound (проверено: «колбэки НЕ вызывались»). Режим
    # перезаписи держим в модульном _OVERWRITE_PARAM_VALUES, экземпляр
    # создаём через make_load_options().

    trace = []  # трейс вызовов колбэков (диагностика)

    def OnFamilyFound(self, familyInUse, overwriteParameterValues):
        try:
            overwriteParameterValues.Value = _OVERWRITE_PARAM_VALUES[0]
            OverwriteFamilyLoadOptions.trace.append(
                u"OnFamilyFound(inUse={}) -> overwrite={}".format(
                    familyInUse, _OVERWRITE_PARAM_VALUES[0]
                )
            )
        except Exception as ex:
            OverwriteFamilyLoadOptions.trace.append(u"OnFamilyFound ИСКЛ: {}".format(ex))
        return True

    def OnSharedFamilyFound(self, sharedFamily, familyInUse, source, overwriteParameterValues):
        try:
            source.Value = FamilySource.Family
            overwriteParameterValues.Value = _OVERWRITE_PARAM_VALUES[0]
            OverwriteFamilyLoadOptions.trace.append(u"OnSharedFamilyFound")
        except Exception as ex:
            OverwriteFamilyLoadOptions.trace.append(u"OnSharedFamilyFound ИСКЛ: {}".format(ex))
        return True


_OVERWRITE_PARAM_VALUES = [True]


def make_load_options(overwrite_parameter_values=True):
    """Экземпляр OverwriteFamilyLoadOptions с нужным режимом перезаписи."""
    _OVERWRITE_PARAM_VALUES[0] = bool(overwrite_parameter_values)
    return OverwriteFamilyLoadOptions()


def _safe_filename(name):
    out = name
    for ch in _BAD_FILENAME_CHARS:
        out = out.replace(ch, u"_")
    return out.strip() or u"family"


def _set_element_name(el, name):
    """
    Присвоение имени элементу: el.Name = ... у Family/FamilySymbol в
    IronPython падает с ошибкой неоднозначного связывания так же, как
    чтение (см. _safe_element_name), поэтому сначала пробуем статическое
    свойство через рефлексию.
    """
    try:
        Element.Name.SetValue(el, name)
        return True
    except:
        try:
            el.Name = name
            return True
        except:
            return False


def _checkout_status(doc, family):
    try:
        from Autodesk.Revit.DB import WorksharingUtils
        return u"{}".format(WorksharingUtils.GetCheckoutStatus(doc, family.Id))
    except Exception as ex:
        return u"?({})".format(ex)


def _checkout_family(doc, family):
    """
    Совместная модель: «одалживает» элемент семейства из центральной
    (WorksharingUtils.CheckoutElements) — программный аналог ручного
    диалога «Семейство не является редактируемым. Сделать редактируемым и
    продолжить? → Да». Без этого Document.LoadFamily молча возвращает
    False, и геометрия/параметры не обновляются (переименование при этом
    проходит, т.к. идёт в явной транзакции). **Вне транзакции.**

    Возвращает строку-примечание (для отчёта-диагностики). Тихо ничего не
    делает для несовместной модели / при недоступности API.
    """
    try:
        if not doc.IsWorkshared:
            return u"модель не совместная"
    except:
        return u"IsWorkshared недоступен"
    before = _checkout_status(doc, family)
    err = u""
    try:
        from Autodesk.Revit.DB import WorksharingUtils
        ids = List[ElementId]()
        ids.Add(family.Id)
        WorksharingUtils.CheckoutElements(doc, ids)
    except Exception as ex:
        err = u" checkout-искл={}".format(ex)
    after = _checkout_status(doc, family)
    return u"co: {} -> {}{}".format(before, after, err)


def _load_rfa_into_project(doc, rfa_path, options):
    """
    Загружает семейство из rfa_path в проект «как из редактора семейств»:
    OpenDocumentFile(.rfa) -> Document.LoadFamily(targetDoc, opts) -> Close.

    Это путь ручного «Загрузить в проект»: он форсирует перезагрузку и
    вызывает IFamilyLoadOptions.OnFamilyFound. Путевой doc.LoadFamily(path,
    opts) молча возвращает False (и не зовёт колбэк), если версия файла не
    новее уже загруженного семейства — типичный случай при обновлении
    каталожной 2020-версии поверх апгрейженной до 2024. Запасной вариант —
    путевой вызов.

    Возвращает (family_or_None, note).
    """
    notes = []
    fam = None
    try:
        fdoc = doc.Application.OpenDocumentFile(rfa_path)
        try:
            fam = fdoc.LoadFamily(doc, options)
            notes.append(u"FamilyDoc.LoadFamily -> {}".format(
                u"ok" if fam is not None else u"None"
            ))
        finally:
            try:
                fdoc.Close(False)
            except Exception as cex:
                notes.append(u"Close искл: {}".format(cex))
    except Exception as ex:
        notes.append(u"FamilyDoc.LoadFamily искл: {}".format(ex))

    if fam is None:
        try:
            res = doc.LoadFamily(rfa_path, options)
            if isinstance(res, tuple):
                if len(res) > 1 and res[1] is not None:
                    fam = res[1]
                notes.append(u"doc.LoadFamily -> {}".format(bool(res[0])))
            else:
                notes.append(u"doc.LoadFamily -> {}".format(bool(res)))
        except Exception as ex:
            notes.append(u"doc.LoadFamily искл: {}".format(ex))

    return (fam, u"; ".join(notes))


def reload_family(doc, src_path, target_family_name, temp_dir, options):
    """
    Перезагружает семейство target_family_name из файла src_path с заменой
    параметров. Файл копируется во temp_dir под именем «<имя семейства>.rfa»,
    т.к. Revit при загрузке сопоставляет семейство по имени файла — так
    файл каталога с другим именем всё равно обновит нужное семейство
    модели, а не создаст новое.

    Грузит через _load_rfa_into_project (OpenDocumentFile -> LoadFamily в
    целевой документ) — это форсирует перезагрузку даже для старой версии
    файла и вызывает OnFamilyFound.

    Возвращает (status, family_element, note):
      ("loaded",    family) — семейство перезагружено;
      ("unchanged", family) — перезагрузка не прошла (вернулся None), но
            семейство в модели найдено (для метки/переименования);
      ("error",     "текст ошибки").
    note — строка-диагностика для отчёта.
    """
    dst = os.path.join(temp_dir, _safe_filename(target_family_name) + u".rfa")
    diag = [u"файл: {}".format(src_path)]

    try:
        shutil.copyfile(src_path, dst)
        # каталог типоразмеров моделируемого семейства — рядом с .rfa
        src_txt = os.path.splitext(src_path)[0] + u".txt"
        dst_txt = os.path.splitext(dst)[0] + u".txt"
        if os.path.isfile(src_txt):
            shutil.copyfile(src_txt, dst_txt)
    except Exception as ex:
        return (u"error", u"копирование во временный файл: {}".format(ex), u"")

    # Совместная модель: одолжить существующее семейство из центральной,
    # иначе Document.LoadFamily молча вернёт False (не может ответить на
    # диалог «Сделать семейство редактируемым?»).
    existing = find_family_by_name(doc, target_family_name)
    if existing is None:
        diag.append(u"семейство «{}» в модели НЕ найдено".format(target_family_name))
    else:
        try:
            n_before = len(list(existing.GetFamilySymbolIds()))
        except:
            n_before = -1
        diag.append(u"типоразмеров до: {}".format(n_before))
        diag.append(_checkout_family(doc, existing))

    del OverwriteFamilyLoadOptions.trace[:]

    cat_types = _read_type_catalog_names(dst_txt) if os.path.isfile(dst_txt) else []
    if cat_types:
        # моделируемое семейство с .txt: LoadFamily целиком берёт только
        # прототип, поэтому грузим типы из каталога через LoadFamilySymbol
        errs = []
        for tn in cat_types:
            try:
                doc.LoadFamilySymbol(dst, tn, options)
            except Exception as ex:
                errs.append(u"«{}»: {}".format(tn, ex))
        fam = find_family_by_name(doc, target_family_name)
        diag.append(u"каталог типов: {} шт{}".format(
            len(cat_types), u" (ошибки: " + u"; ".join(errs) + u")" if errs else u""
        ))
    else:
        fam, note = _load_rfa_into_project(doc, dst, options)
        diag.append(note)

    # реально перезагрузилось, только если вернулся элемент Family
    changed = fam is not None

    if OverwriteFamilyLoadOptions.trace:
        diag.append(u"колбэки: " + u" | ".join(OverwriteFamilyLoadOptions.trace))
    else:
        diag.append(u"колбэки НЕ вызывались")

    loaded = fam
    try:
        if loaded is None or not loaded.IsValidObject:
            loaded = None
    except:
        loaded = None
    if loaded is None:
        # ссылка для метки/переименования (даже если перезагрузка не прошла)
        loaded = find_family_by_name(doc, target_family_name)
    diag.append(u"ссылка: {}".format(u"есть" if loaded is not None else u"НЕТ"))

    try:
        if loaded is not None:
            diag.append(u"типоразмеров после: {}".format(len(list(loaded.GetFamilySymbolIds()))))
    except:
        pass

    return (u"loaded" if changed else u"unchanged", loaded, u"; ".join(diag))


def rename_family(doc, family, new_name):
    """
    Переименовывает семейство модели в new_name (например по имени файла
    каталога). **Требует открытой транзакции.** Возвращает (True, None)
    либо (False, "причина") — если имя уже совпадает, занято другим
    семейством, пустое или Revit его отклонил.
    """
    try:
        new_name = unicode(new_name or u"").strip()
        if not new_name:
            return (False, u"пустое имя файла")
        if _safe_element_name(family) == new_name:
            return (False, u"имя уже совпадает")

        existing = find_family_by_name(doc, new_name)
        if existing is not None and existing.Id != family.Id:
            return (False, u"в проекте уже есть семейство «{}»".format(new_name))

        if _set_element_name(family, new_name):
            return (True, None)
        return (False, u"Revit отклонил имя «{}»".format(new_name))
    except Exception as ex:
        return (False, u"{}".format(ex))


# --------------------------------------------------------------------------
# Общие помощники WPF-таблиц (DataGrid)
# --------------------------------------------------------------------------

class EntryOption(object):
    """CatalogEntry для forms.SelectFromList при ручной смене файла."""

    def __init__(self, entry):
        self.entry = entry
        self.name = entry.rel

    def __str__(self):
        return self.name


def _star(n):
    return DataGridLength(n, DataGridLengthUnitType.Star)


def _text_col(header, path, width=None, sort_path=None):
    c = DataGridTextColumn()
    c.Header = header
    c.Binding = Binding(path)
    c.IsReadOnly = True
    c.SortMemberPath = sort_path or path
    c.Width = width if width is not None else DataGridLength.Auto
    return c


def _check_col(header, path="Selected"):
    b = Binding(path)
    b.Mode = BindingMode.TwoWay
    b.UpdateSourceTrigger = UpdateSourceTrigger.PropertyChanged
    c = DataGridCheckBoxColumn()
    c.Header = header
    c.Binding = b
    c.CanUserSort = False
    return c


def _attach_row_coloring(grid, brush_fn):
    """Красит текст строки DataGrid кистью brush_fn(item); переживает
    прокрутку/пересортировку (LoadingRow вызывается повторно)."""
    def on_loading_row(sender, e):
        try:
            e.Row.Foreground = brush_fn(e.Row.Item)
        except:
            pass
    grid.LoadingRow += on_loading_row


def _attach_datagrid_sorting(grid, data):
    """
    Своя сортировка по клику на заголовок: WPF не разрешает пути к
    python-объектам в SortDescription, поэтому сортируем список data
    (List[object]) сами по column.SortMemberPath.
    """
    state = {"path": None, "asc": True}

    def _key(row, path):
        val = getattr(row, path, None)
        if isinstance(val, basestring):
            return (0, val.lower())
        if val is None:
            return (1, u"")
        return (0, val)

    def on_sorting(sender, e):
        col = e.Column
        path = col.SortMemberPath
        if not path:
            e.Handled = True
            return
        asc = not (state["path"] == path and state["asc"])
        state["path"] = path
        state["asc"] = asc

        items = sorted(list(data), key=lambda r: _key(r, path), reverse=not asc)
        data.Clear()
        for it in items:
            data.Add(it)
        try:
            grid.Items.Refresh()
        except:
            pass

        for c in grid.Columns:
            c.SortDirection = None
        col.SortDirection = (
            ListSortDirection.Ascending if asc else ListSortDirection.Descending
        )
        e.Handled = True

    grid.Sorting += on_sorting


# --------------------------------------------------------------------------
# Окно каталога: актуальность семейств + обновление отмеченных
# --------------------------------------------------------------------------

def _status_brush(status):
    if status == STATUS_STALE:
        return Brushes.Firebrick
    if status == STATUS_NO_STAMP:
        return Brushes.DarkOrange
    if status == STATUS_CURRENT:
        return Brushes.Green
    return Brushes.Gray


class _StatusRow(object):
    """Строка DataGrid окна каталога (свойства читаются WPF-биндингом)."""

    def __init__(self, mr):
        self.mr = mr
        self.FamilyName = mr.family_name
        try:
            self.Category = mr.family.FamilyCategory.Name
        except:
            self.Category = u""
        self.Selected = False
        self._refresh()

    def _refresh(self):
        mr = self.mr
        self.Status = mr.status
        self.ModelDate = (mr.stamp or {}).get("iso") or u"—"
        self.CatalogDate = (mr.entry.mtime_iso if mr.entry and mr.entry.mtime_iso else u"—")
        self.CatalogFile = mr.entry.rel if mr.entry else u"—"
        self.Score = float(mr.score)
        self.ScoreText = (u"{}%".format(int(round(mr.score * 100))) if mr.entry else u"—")


def show_status_form(rows, catalog_root, entries):
    """
    Единое окно каталога: информация + сортируемая таблица (семейство,
    категория, статус зелёным/красным, даты, файл, похожесть) с галочками
    выбора. Двойной клик по строке или кнопка «Файл…» — вручную указать
    другой файл каталога для строки.

    Возвращает (jobs, do_rename, overwrite_params) для отмеченных строк с
    файлом в каталоге, либо None, если окно просто закрыли.
    jobs — (family, src_path, target_family_name, display, catalog_name).
    """
    counts = {STATUS_STALE: 0, STATUS_NO_STAMP: 0, STATUS_NO_CATALOG: 0, STATUS_CURRENT: 0}
    for mr in rows:
        counts[mr.status] = counts.get(mr.status, 0) + 1

    data = List[object]()
    for mr in rows:
        row = _StatusRow(mr)
        row.Selected = bool(mr.entry) and mr.status in (STATUS_STALE, STATUS_NO_STAMP)
        data.Add(row)

    entry_options = sorted([EntryOption(e) for e in entries], key=lambda o: o.name.lower())
    result = {"jobs": None, "rename": True, "overwrite": True}

    win = Window()
    win.Title = u"Семейства из каталога — актуальность и обновление"
    win.Width = 1120
    win.Height = 720
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen

    outer = DockPanel()
    outer.LastChildFill = True

    header = StackPanel()
    header.Margin = Thickness(16, 12, 16, 8)
    DockPanel.SetDock(header, Dock.Top)

    title = TextBlock()
    title.Text = u"Актуальность семейств относительно каталога"
    title.FontSize = 16
    title.FontWeight = FontWeights.Bold
    header.Children.Add(title)

    info = TextBlock()
    info.Text = (
        u"Каталог: {}\n"
        u"Устарели: {}   ·   без метки: {}   ·   нет в каталоге: {}   ·   актуальны: {}\n"
        u"Красным — требуют обновления, оранжевым — без метки, зелёным — актуальны. "
        u"Клик по заголовку — сортировка. Двойной клик по строке или «Файл…» — "
        u"выбрать другой файл каталога. Отметьте, что обновить, и нажмите "
        u"«Обновить отмеченные»; чтобы просто посмотреть — закройте окно.".format(
            catalog_root,
            counts.get(STATUS_STALE, 0), counts.get(STATUS_NO_STAMP, 0),
            counts.get(STATUS_NO_CATALOG, 0), counts.get(STATUS_CURRENT, 0)
        )
    )
    info.FontSize = 11
    info.Foreground = Brushes.Gray
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

    grid.Columns.Add(_check_col(u"Обновить"))
    grid.Columns.Add(_text_col(u"Семейство", "FamilyName", _star(3)))
    grid.Columns.Add(_text_col(u"Категория", "Category", _star(2)))
    grid.Columns.Add(_text_col(u"Статус", "Status"))
    grid.Columns.Add(_text_col(u"Дата в модели", "ModelDate"))
    grid.Columns.Add(_text_col(u"Дата в каталоге", "CatalogDate"))
    grid.Columns.Add(_text_col(u"Файл каталога", "CatalogFile", _star(3)))
    grid.Columns.Add(_text_col(u"Похожесть", "ScoreText", sort_path="Score"))

    _attach_row_coloring(grid, lambda it: _status_brush(it.Status))
    _attach_datagrid_sorting(grid, data)

    def _choose_file(row):
        if row is None:
            forms.alert(u"Сначала выделите строку в таблице.")
            return
        sel = forms.SelectFromList.show(
            entry_options,
            title=u"Файл каталога для «{}»".format(row.mr.family_name),
            button_name=u"Выбрать",
            multiselect=False
        )
        if not sel:
            return
        e = sel.entry
        row.mr.entry = e
        row.mr.score = similarity(row.mr.family_name, e.name)
        row.mr.status = stamp_status(row.mr.stamp, e)
        row._refresh()
        row.Selected = True
        try:
            grid.Items.Refresh()
        except:
            pass

    grid.MouseDoubleClick += lambda s, a: _choose_file(grid.SelectedItem)

    bottom = StackPanel()
    bottom.Margin = Thickness(16, 8, 16, 12)
    DockPanel.SetDock(bottom, Dock.Bottom)

    overwrite_cb = CheckBox()
    overwrite_cb.Content = (
        u"Заменять значения параметров типоразмеров из файла каталога "
        u"(в браузере — «Перезаписать существующую версию и значения параметров»)"
    )
    overwrite_cb.IsChecked = True
    overwrite_cb.Margin = Thickness(0, 0, 0, 6)
    bottom.Children.Add(overwrite_cb)

    rename_cb = CheckBox()
    rename_cb.Content = (
        u"Переименовывать семейство модели по имени файла каталога, если они различаются"
    )
    rename_cb.IsChecked = True
    rename_cb.Margin = Thickness(0, 0, 0, 8)
    bottom.Children.Add(rename_cb)

    buttons = StackPanel()
    buttons.Orientation = Orientation.Horizontal
    buttons.HorizontalAlignment = HorizontalAlignment.Right
    bottom.Children.Add(buttons)

    def _select_where(pred):
        try:
            grid.CommitEdit()
        except:
            pass
        for row in data:
            row.Selected = pred(row)
        grid.Items.Refresh()

    file_btn = Button()
    file_btn.Content = u"Файл…"
    file_btn.Padding = Thickness(10, 4, 10, 4)
    file_btn.Margin = Thickness(0, 0, 8, 0)
    file_btn.Click += lambda s, a: _choose_file(grid.SelectedItem)

    sel_stale_btn = Button()
    sel_stale_btn.Content = u"Отметить требующие обновления"
    sel_stale_btn.Padding = Thickness(10, 4, 10, 4)
    sel_stale_btn.Margin = Thickness(0, 0, 8, 0)
    sel_stale_btn.Click += lambda s, a: _select_where(
        lambda r: r.mr.entry is not None and r.mr.status in (STATUS_STALE, STATUS_NO_STAMP)
    )

    sel_none_btn = Button()
    sel_none_btn.Content = u"Снять все"
    sel_none_btn.Padding = Thickness(10, 4, 10, 4)
    sel_none_btn.Margin = Thickness(0, 0, 8, 0)
    sel_none_btn.Click += lambda s, a: _select_where(lambda r: False)

    close_btn = Button()
    close_btn.Content = u"Закрыть"
    close_btn.Padding = Thickness(10, 4, 10, 4)
    close_btn.Margin = Thickness(0, 0, 8, 0)

    run_btn = Button()
    run_btn.Content = u"Обновить отмеченные"
    run_btn.Padding = Thickness(10, 4, 10, 4)
    run_btn.FontWeight = FontWeights.Bold

    def on_run(sender, args):
        try:
            grid.CommitEdit()
        except:
            pass
        jobs = []
        for row in data:
            if not row.Selected or row.mr.entry is None:
                continue
            jobs.append((
                row.mr.family,
                row.mr.entry.path,
                row.mr.family_name,
                row.mr.entry.rel,
                row.mr.entry.name,
            ))
        if not jobs:
            forms.alert(u"Не отмечено ни одного семейства с файлом в каталоге.")
            return
        result["jobs"] = jobs
        result["rename"] = bool(rename_cb.IsChecked)
        result["overwrite"] = bool(overwrite_cb.IsChecked)
        win.Close()

    def on_close(sender, args):
        win.Close()

    run_btn.Click += on_run
    close_btn.Click += on_close

    buttons.Children.Add(file_btn)
    buttons.Children.Add(sel_stale_btn)
    buttons.Children.Add(sel_none_btn)
    buttons.Children.Add(close_btn)
    buttons.Children.Add(run_btn)

    outer.Children.Add(header)
    outer.Children.Add(bottom)
    outer.Children.Add(grid)

    win.Content = outer
    win.ShowDialog()

    if result["jobs"] is None:
        return None
    return (result["jobs"], result["rename"], result["overwrite"])


# --------------------------------------------------------------------------
# Применение обновлений (общее для обеих кнопок)
# --------------------------------------------------------------------------

def apply_updates(doc, jobs, do_rename, overwrite_params=True):
    """
    jobs — список (family, src_path, target_family_name, display, catalog_name).
    overwrite_params — см. OverwriteFamilyLoadOptions (True = «перезаписать и
    значения параметров», как в диалоге браузера).

    Грузит каждое семейство (reload_family, вне транзакции), затем одной
    транзакцией: при do_rename и различии имён переименовывает семейство
    модели по имени файла каталога (rename_family) и пишет скрытую метку
    даты (write_stamp).

    Возвращает dict со списками для отчёта:
      updated       — (final_name, display, iso)  содержимое перезагружено;
      unchanged     — (final_name, display, iso)  LoadFamily=False, совпало;
      renamed       — (old_name, new_name);
      rename_failed — (old_name, new_name, err);
      failed        — (target_name, display, err) ошибка загрузки;
      stamp_failed  — final_name.
    """
    result = {
        "updated": [], "unchanged": [], "renamed": [],
        "rename_failed": [], "failed": [], "stamp_failed": [], "debug": [],
    }
    if not jobs:
        return result

    options = make_load_options(overwrite_params)
    temp_dir = tempfile.mkdtemp(prefix="lowlife_famcat_")
    loaded = []

    try:
        for family, src_path, target_name, disp, catalog_name in jobs:
            status, payload, note = reload_family(doc, src_path, target_name, temp_dir, options)
            result["debug"].append(u"**{}** — {}".format(target_name, note))
            if status == u"error":
                result["failed"].append((target_name, disp, payload))
            else:
                loaded.append((payload, target_name, disp, src_path, catalog_name, status))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    if not loaded:
        return result

    pname = load_stamp_param_name()

    t = Transaction(doc, u"Обновление семейств из каталога: имена и метки")
    t.Start()
    try:
        for fam_elem, target_name, disp, src_path, catalog_name, status in loaded:
            epoch, iso = file_mtime(src_path)
            final_name = target_name
            was_renamed = False

            if do_rename and fam_elem and catalog_name and catalog_name != target_name:
                ok_rn, err_rn = rename_family(doc, fam_elem, catalog_name)
                if ok_rn:
                    result["renamed"].append((target_name, catalog_name))
                    final_name = catalog_name
                    was_renamed = True
                else:
                    result["rename_failed"].append((target_name, catalog_name, err_rn))

            ok_stamp = write_stamp(fam_elem, epoch, iso, disp) if fam_elem else False
            if not ok_stamp:
                result["stamp_failed"].append(final_name)
            if pname and fam_elem:
                write_stamp_param(doc, fam_elem, iso, pname)

            if status == u"loaded":
                result["updated"].append((final_name, disp, iso))
            elif not was_renamed:
                result["unchanged"].append((final_name, disp, iso))
        t.Commit()
    except:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise

    return result


def render_result_md(output, result):
    """Печатает разделы отчёта apply_updates в окно вывода pyRevit."""
    up = result["updated"]
    ch = result["unchanged"]
    rn = result["renamed"]
    rf = result["rename_failed"]
    fl = result["failed"]
    sf = result["stamp_failed"]

    if up:
        output.print_md(u"### Обновлены семейства ({})".format(len(up)))
        output.print_md(u"\n".join(
            u"- **{}**  ←  `{}`  _(файл {})_".format(n, d, i or u"?") for n, d, i in up
        ))
    if rn:
        output.print_md(u"### Переименованы по файлу каталога ({})".format(len(rn)))
        output.print_md(u"\n".join(u"- «{}»  →  «{}»".format(a, b) for a, b in rn))
    if ch:
        output.print_md(u"### Без изменений — содержимое совпадает ({})".format(len(ch)))
        output.print_md(u"\n".join(
            u"- **{}**  ←  `{}`  _(файл {})_".format(n, d, i or u"?") for n, d, i in ch
        ))
    if fl:
        output.print_md(u"### Не удалось загрузить ({})".format(len(fl)))
        output.print_md(u"\n".join(
            u"- **{}**  ←  `{}` — {}".format(n, d, e) for n, d, e in fl
        ))
    if rf:
        output.print_md(u"### Обновлены, но не переименованы ({})".format(len(rf)))
        output.print_md(u"\n".join(
            u"- «{}»  →  «{}» — {}".format(a, b, e) for a, b, e in rf
        ))
    if sf:
        output.print_md(u"### Метку даты записать не удалось ({})\n{}".format(
            len(sf), u"\n".join(u"- {}".format(x) for x in sf)
        ))
    dbg = result.get("debug")
    if dbg:
        output.print_md(u"### Диагностика LoadFamily")
        output.print_md(u"\n".join(u"- {}".format(x) for x in dbg))


def result_summary_lines(result):
    """Короткая сводка apply_updates для forms.alert."""
    lines = [u"Обновлено: {}".format(len(result["updated"]))]
    if result["renamed"]:
        lines.append(u"Переименовано: {}".format(len(result["renamed"])))
    if result["unchanged"]:
        lines.append(u"Без изменений: {}".format(len(result["unchanged"])))
    if result["failed"]:
        lines.append(u"Ошибок загрузки: {}".format(len(result["failed"])))
    if result["rename_failed"]:
        lines.append(u"Не переименовано: {}".format(len(result["rename_failed"])))
    if result["stamp_failed"]:
        lines.append(u"Без метки даты: {}".format(len(result["stamp_failed"])))
    return lines


# --------------------------------------------------------------------------
# Окно загрузки семейств из каталога (кнопка «Загрузить семейства»)
# --------------------------------------------------------------------------

class _LoadRow(object):
    """Строка DataGrid окна загрузки."""

    def __init__(self, entry, in_model):
        self.entry = entry
        self.FileName = entry.name
        self.Folder = os.path.dirname(entry.rel) or u"."
        self.InModel = bool(in_model)
        self.InModelText = u"да" if in_model else u"нет"
        self.CatalogDate = entry.mtime_iso or u"—"
        self.Selected = not in_model


def show_load_form(entries, present_names, catalog_root):
    """
    Окно выбора семейств каталога для загрузки в модель. Возвращает
    (entries, overwrite_params) — список CatalogEntry для отмеченных строк
    и флаг замены значений параметров, — либо None, если окно закрыли.
    Выбор типоразмеров делается всегда, отдельным окном (show_type_picker).
    """
    data = List[object]()
    n_new = 0
    for e in entries:
        in_model = e.name in present_names
        if not in_model:
            n_new += 1
        data.Add(_LoadRow(e, in_model))

    result = {"entries": None, "overwrite": True}

    win = Window()
    win.Title = u"Загрузка семейств из каталога"
    win.Width = 1000
    win.Height = 720
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen

    outer = DockPanel()
    outer.LastChildFill = True

    header = StackPanel()
    header.Margin = Thickness(16, 12, 16, 8)
    DockPanel.SetDock(header, Dock.Top)

    title = TextBlock()
    title.Text = u"Загрузить семейства из каталога в модель"
    title.FontSize = 16
    title.FontWeight = FontWeights.Bold
    header.Children.Add(title)

    info = TextBlock()
    info.Text = (
        u"Каталог: {}\n"
        u"Файлов: {}   ·   новых (нет в модели): {}   ·   уже в модели: {}\n"
        u"Зелёным — новые, серым — уже загружены (будут перезагружены с заменой "
        u"параметров). Клик по заголовку — сортировка. Отметьте нужные и нажмите "
        u"«Загрузить отмеченные».".format(
            catalog_root, len(entries), n_new, len(entries) - n_new
        )
    )
    info.FontSize = 11
    info.Foreground = Brushes.Gray
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
    grid.SelectionMode = DataGridSelectionMode.Extended
    grid.IsReadOnly = False
    grid.ItemsSource = data

    grid.Columns.Add(_check_col(u"Загрузить"))
    grid.Columns.Add(_text_col(u"Файл", "FileName", _star(3)))
    grid.Columns.Add(_text_col(u"Папка", "Folder", _star(2)))
    grid.Columns.Add(_text_col(u"В модели", "InModelText", sort_path="InModel"))
    grid.Columns.Add(_text_col(u"Дата файла", "CatalogDate"))

    _attach_row_coloring(grid, lambda it: Brushes.Gray if it.InModel else Brushes.Green)
    _attach_datagrid_sorting(grid, data)

    bottom = StackPanel()
    bottom.Margin = Thickness(16, 8, 16, 12)
    DockPanel.SetDock(bottom, Dock.Bottom)

    overwrite_cb = CheckBox()
    overwrite_cb.Content = (
        u"Для уже присутствующих в модели — заменять значения параметров типоразмеров "
        u"из файла каталога (для новых семейств роли не играет)"
    )
    overwrite_cb.IsChecked = True
    overwrite_cb.Margin = Thickness(0, 0, 0, 8)
    bottom.Children.Add(overwrite_cb)

    buttons = StackPanel()
    buttons.Orientation = Orientation.Horizontal
    buttons.HorizontalAlignment = HorizontalAlignment.Right
    bottom.Children.Add(buttons)

    def _select_where(pred):
        try:
            grid.CommitEdit()
        except:
            pass
        for row in data:
            row.Selected = pred(row)
        grid.Items.Refresh()

    sel_new_btn = Button()
    sel_new_btn.Content = u"Отметить новые"
    sel_new_btn.Padding = Thickness(10, 4, 10, 4)
    sel_new_btn.Margin = Thickness(0, 0, 8, 0)
    sel_new_btn.Click += lambda s, a: _select_where(lambda r: not r.InModel)

    sel_all_btn = Button()
    sel_all_btn.Content = u"Отметить все"
    sel_all_btn.Padding = Thickness(10, 4, 10, 4)
    sel_all_btn.Margin = Thickness(0, 0, 8, 0)
    sel_all_btn.Click += lambda s, a: _select_where(lambda r: True)

    sel_none_btn = Button()
    sel_none_btn.Content = u"Снять все"
    sel_none_btn.Padding = Thickness(10, 4, 10, 4)
    sel_none_btn.Margin = Thickness(0, 0, 8, 0)
    sel_none_btn.Click += lambda s, a: _select_where(lambda r: False)

    close_btn = Button()
    close_btn.Content = u"Закрыть"
    close_btn.Padding = Thickness(10, 4, 10, 4)
    close_btn.Margin = Thickness(0, 0, 8, 0)

    run_btn = Button()
    run_btn.Content = u"Загрузить отмеченные"
    run_btn.Padding = Thickness(10, 4, 10, 4)
    run_btn.FontWeight = FontWeights.Bold

    def on_run(sender, args):
        try:
            grid.CommitEdit()
        except:
            pass
        picked = [row.entry for row in data if row.Selected]
        if not picked:
            forms.alert(u"Не отмечено ни одного семейства.")
            return
        result["entries"] = picked
        result["overwrite"] = bool(overwrite_cb.IsChecked)
        win.Close()

    def on_close(sender, args):
        win.Close()

    run_btn.Click += on_run
    close_btn.Click += on_close

    buttons.Children.Add(sel_new_btn)
    buttons.Children.Add(sel_all_btn)
    buttons.Children.Add(sel_none_btn)
    buttons.Children.Add(close_btn)
    buttons.Children.Add(run_btn)

    outer.Children.Add(header)
    outer.Children.Add(bottom)
    outer.Children.Add(grid)

    win.Content = outer
    win.ShowDialog()

    if result["entries"] is None:
        return None
    return (result["entries"], result["overwrite"])


class _TypeRow(object):
    """Строка DataGrid окна выбора типоразмеров."""

    def __init__(self, entry, family_name, type_name):
        self.entry = entry
        self.FamilyName = family_name
        self.TypeName = type_name
        self.Selected = True


def show_type_picker(type_map):
    """
    type_map — список (entry, [имена_типоразмеров]). Окно с галочками
    по каждому типоразмеру каждого семейства. Возвращает:
      {entry: set(отмеченные_имена)} — по кнопке «Загрузить отмеченные»;
      BACK — по кнопке «← Назад» (вернуться к окну выбора файлов);
      None — окно закрыто.
    """
    if not type_map:
        return {}

    data = List[object]()
    for entry, names in type_map:
        fam_name = os.path.splitext(os.path.basename(entry.path))[0]
        for tn in names:
            data.Add(_TypeRow(entry, fam_name, tn))

    result = {"map": None}

    win = Window()
    win.Title = u"Выбор типоразмеров для загрузки"
    win.Width = 820
    win.Height = 640
    win.WindowStartupLocation = WindowStartupLocation.CenterScreen

    outer = DockPanel()
    outer.LastChildFill = True

    header = StackPanel()
    header.Margin = Thickness(16, 12, 16, 8)
    DockPanel.SetDock(header, Dock.Top)

    title = TextBlock()
    title.Text = u"Отметьте типоразмеры для загрузки в модель"
    title.FontSize = 16
    title.FontWeight = FontWeights.Bold
    header.Children.Add(title)

    info = TextBlock()
    info.Text = (
        u"Семейств: {}   ·   типоразмеров всего: {}\n"
        u"Снятые типоразмеры не загружаются. Если у семейства отмечены все "
        u"типоразмеры — оно грузится целиком.".format(len(type_map), data.Count)
    )
    info.FontSize = 11
    info.Foreground = Brushes.Gray
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
    grid.SelectionMode = DataGridSelectionMode.Extended
    grid.IsReadOnly = False
    grid.ItemsSource = data

    grid.Columns.Add(_check_col(u"Загрузить"))
    grid.Columns.Add(_text_col(u"Семейство", "FamilyName", _star(2)))
    grid.Columns.Add(_text_col(u"Типоразмер", "TypeName", _star(3)))

    _attach_datagrid_sorting(grid, data)

    bottom = StackPanel()
    bottom.Margin = Thickness(16, 8, 16, 12)
    DockPanel.SetDock(bottom, Dock.Bottom)

    buttons = StackPanel()
    buttons.Orientation = Orientation.Horizontal
    buttons.HorizontalAlignment = HorizontalAlignment.Right
    bottom.Children.Add(buttons)

    def _select_all(val):
        try:
            grid.CommitEdit()
        except:
            pass
        for row in data:
            row.Selected = val
        grid.Items.Refresh()

    all_btn = Button()
    all_btn.Content = u"Отметить все"
    all_btn.Padding = Thickness(10, 4, 10, 4)
    all_btn.Margin = Thickness(0, 0, 8, 0)
    all_btn.Click += lambda s, a: _select_all(True)

    none_btn = Button()
    none_btn.Content = u"Снять все"
    none_btn.Padding = Thickness(10, 4, 10, 4)
    none_btn.Margin = Thickness(0, 0, 8, 0)
    none_btn.Click += lambda s, a: _select_all(False)

    back_btn = Button()
    back_btn.Content = u"← Назад"
    back_btn.Padding = Thickness(10, 4, 10, 4)
    back_btn.Margin = Thickness(0, 0, 8, 0)

    close_btn = Button()
    close_btn.Content = u"Закрыть"
    close_btn.Padding = Thickness(10, 4, 10, 4)
    close_btn.Margin = Thickness(0, 0, 8, 0)

    run_btn = Button()
    run_btn.Content = u"Загрузить отмеченные"
    run_btn.Padding = Thickness(10, 4, 10, 4)
    run_btn.FontWeight = FontWeights.Bold

    def on_run(sender, args):
        try:
            grid.CommitEdit()
        except:
            pass
        picked = {}
        for row in data:
            if row.Selected:
                picked.setdefault(row.entry, set()).add(row.TypeName)
        if not picked:
            forms.alert(u"Не отмечено ни одного типоразмера.")
            return
        result["map"] = picked
        win.Close()

    def on_back(sender, args):
        result["map"] = BACK
        win.Close()

    def on_close(sender, args):
        win.Close()

    run_btn.Click += on_run
    back_btn.Click += on_back
    close_btn.Click += on_close

    buttons.Children.Add(back_btn)
    buttons.Children.Add(all_btn)
    buttons.Children.Add(none_btn)
    buttons.Children.Add(close_btn)
    buttons.Children.Add(run_btn)

    outer.Children.Add(header)
    outer.Children.Add(bottom)
    outer.Children.Add(grid)

    win.Content = outer
    win.ShowDialog()

    return result["map"]


def _read_type_catalog_names(txt_path):
    """
    Имена типоразмеров из каталога типоразмеров .txt (первый столбец каждой
    строки после заголовка; разделитель — первый символ первой строки).
    [] при сбое. Кодировка каталога — UTF-16 / UTF-8 / ANSI (cp1251).
    """
    lines = None
    for enc in (u"utf-16", u"utf-8-sig", u"cp1251", u"utf-8", u"latin-1"):
        try:
            with io.open(txt_path, "r", encoding=enc) as f:
                text = f.read()
        except:
            continue
        if u"�" in text and enc != u"latin-1":
            continue
        lines = text.splitlines()
        break
    if not lines:
        return []

    header = lines[0] if lines[0] else u","
    delim = header[0] if header[0] in (u",", u";", u"\t") else u","

    names = []
    for ln in lines[1:]:
        ln = ln.strip()
        if not ln:
            continue
        name = ln.split(delim, 1)[0].strip().strip(u'"')
        if name:
            names.append(name)
    return names


def read_family_type_names(app, path):
    """
    Имена типоразмеров семейства. Если рядом лежит каталог типоразмеров
    .txt — берём из него (у моделируемых семейств типы именно там, в
    FamilyManager.Types только прототип). Иначе открываем .rfa отдельным
    документом и читаем FamilyManager.Types. [] при сбое. ВНЕ транзакции.
    """
    txt = os.path.splitext(path)[0] + u".txt"
    if os.path.isfile(txt):
        cat = _read_type_catalog_names(txt)
        if cat:
            return sorted(set(cat))

    names = []
    fdoc = None
    try:
        fdoc = app.OpenDocumentFile(path)
        for t in fdoc.FamilyManager.Types:
            try:
                if t.Name:
                    names.append(t.Name)
            except:
                pass
    except:
        names = []
    finally:
        if fdoc is not None:
            try:
                fdoc.Close(False)
            except:
                pass
    return sorted(set(names))


def apply_loads(doc, jobs, present_names, overwrite_params=True):
    """
    Загружает выбранные семейства/типоразмеры из каталога в модель напрямую
    (без переименования) и ставит семейству скрытую метку даты.

    jobs — список (entry, type_names): type_names is None → грузить семейство
    целиком (Document.LoadFamily); список имён → грузить только эти
    типоразмеры (Document.LoadFamilySymbol на каждый).

    Новые семейства — «loaded», уже бывшие в модели — «updated».
    overwrite_params — см. OverwriteFamilyLoadOptions (влияет только на уже
    присутствующие). Возвращает dict: loaded / updated / failed / stamp_failed.
    """
    result = {"loaded": [], "updated": [], "failed": [], "stamp_failed": []}
    if not jobs:
        return result

    options = make_load_options(overwrite_params)
    done = []  # (entry, family, was_present, n_types)

    for e, type_names in jobs:
        was_present = e.name in present_names

        # совместная модель: если семейство уже есть — одолжить его
        existing = find_family_by_name(doc, e.name)
        if existing is not None:
            _checkout_family(doc, existing)

        # Модель типоразмеров:
        #  - явный список из окна выбора → грузим его;
        #  - иначе, если рядом с .rfa есть одноимённый .txt (каталог
        #    типоразмеров моделируемого семейства) → грузим все типы из него
        #    через LoadFamilySymbol (LoadFamily целиком берёт только прототип);
        #  - иначе (типы встроены в .rfa, напр. схемные узлы) → обычный способ.
        txt = os.path.splitext(e.path)[0] + u".txt"
        want_types = None
        if type_names:
            want_types = list(type_names)
        elif os.path.isfile(txt):
            want_types = _read_type_catalog_names(txt)

        n_types = None
        try:
            if want_types:
                errs = []
                for tn in want_types:
                    try:
                        doc.LoadFamilySymbol(e.path, tn, options)
                    except Exception as ex:
                        errs.append(u"«{}»: {}".format(tn, ex))
                fam = find_family_by_name(doc, e.name)
                n_types = len(want_types)
                if fam is None and not was_present:
                    result["failed"].append((
                        e.name, e.rel,
                        u"типы из каталога не загрузились: {}".format(
                            u"; ".join(errs) or u"?"
                        )
                    ))
                    continue
            else:
                fam, _note = _load_rfa_into_project(doc, e.path, options)
                try:
                    if fam is None or not fam.IsValidObject:
                        fam = None
                except:
                    fam = None
                if fam is None:
                    fam = find_family_by_name(doc, e.name)
        except Exception as ex:
            result["failed"].append((e.name, e.rel, u"{}".format(ex)))
            continue

        if fam is None and not was_present:
            result["failed"].append((e.name, e.rel, u"семейство не загрузилось"))
            continue

        done.append((e, fam, was_present, n_types))

    if not done:
        return result

    pname = load_stamp_param_name()

    t = Transaction(doc, u"Загрузка семейств из каталога: метки даты")
    t.Start()
    try:
        for e, fam, was_present, n_types in done:
            nm = (_safe_element_name(fam) if fam else None) or e.name
            disp = e.rel if n_types is None else u"{} · типоразмеров: {}".format(e.rel, n_types)
            ok_stamp = write_stamp(fam, e.mtime, e.mtime_iso, e.rel) if fam else False
            if not ok_stamp:
                result["stamp_failed"].append(nm)
            if pname and fam:
                write_stamp_param(doc, fam, e.mtime_iso, pname)
            bucket = "updated" if was_present else "loaded"
            result[bucket].append((nm, disp, e.mtime_iso))
        t.Commit()
    except:
        if t.HasStarted() and not t.HasEnded():
            t.RollBack()
        raise

    return result


def render_load_result_md(output, result):
    ld = result["loaded"]
    up = result["updated"]
    fl = result["failed"]
    sf = result["stamp_failed"]

    if ld:
        output.print_md(u"### Загружены новые семейства ({})".format(len(ld)))
        output.print_md(u"\n".join(
            u"- **{}**  ←  `{}`  _(файл {})_".format(n, d, i or u"?") for n, d, i in ld
        ))
    if up:
        output.print_md(u"### Перезагружены (уже были в модели) ({})".format(len(up)))
        output.print_md(u"\n".join(
            u"- **{}**  ←  `{}`  _(файл {})_".format(n, d, i or u"?") for n, d, i in up
        ))
    if fl:
        output.print_md(u"### Не удалось загрузить ({})".format(len(fl)))
        output.print_md(u"\n".join(
            u"- **{}**  ←  `{}` — {}".format(n, d, e) for n, d, e in fl
        ))
    if sf:
        output.print_md(u"### Метку даты записать не удалось ({})\n{}".format(
            len(sf), u"\n".join(u"- {}".format(x) for x in sf)
        ))


def load_summary_lines(result):
    lines = [u"Загружено новых: {}".format(len(result["loaded"]))]
    if result["updated"]:
        lines.append(u"Перезагружено: {}".format(len(result["updated"])))
    if result["failed"]:
        lines.append(u"Ошибок: {}".format(len(result["failed"])))
    if result["stamp_failed"]:
        lines.append(u"Без метки даты: {}".format(len(result["stamp_failed"])))
    return lines
