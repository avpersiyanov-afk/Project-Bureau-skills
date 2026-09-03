# -*- coding: utf-8 -*-
"""
Выгрузка спецификации в таблицу и обратная загрузка правок в модель.

Ключ связи строки Excel с элементом — «Revit ID» (первый столбец).
Столбцы — параметры по именам полей спецификации; на загрузке ищем
параметр по имени столбца через LookupParameter. Только параметры
экземпляра, только записываемые (не read-only, не ссылки на элементы).

Порядок строк повторяет спецификацию: воспроизводим её поля
сортировки/группировки (`GetSortGroupField`), строки — по одному
экземпляру (без схлопывания одинаковых, чтобы можно было править
каждый по отдельности).
"""

import re

from Autodesk.Revit.DB import (
    Element,
    ElementId,
    FilteredElementCollector,
    ScheduleSortOrder,
    StorageType,
    ViewSchedule,
)

ID_HEADER = u"Revit ID"
_ID_ALIASES = (u"revit id", u"id", u"ид", u"элемент id", u"elementid")

_DIGITS = re.compile(r"(\d+)")
_MM_IN_FOOT = 304.8
# Ширина столбца Excel — в «символах» шрифта по умолчанию: примерно
# (пиксели - 5) / 7, а пиксели ≈ мм * 96 / 25.4.
_ID_COL_WIDTH = 11.0


def _excel_col_width(width_ft):
    u"""Ширина столбца Revit (внутренние футы) -> ширина столбца Excel."""
    try:
        px = width_ft * _MM_IN_FOOT * 96.0 / 25.4
        chars = (px - 5.0) / 7.0
    except Exception:
        return 0.0
    if chars < 3.0:
        return 3.0
    if chars > 120.0:
        return 120.0
    return chars


def schedule_name(el):
    try:
        return Element.Name.GetValue(el)
    except Exception:
        try:
            return el.Name
        except Exception:
            return u"ID {}".format(el.Id.IntegerValue)


def list_schedules(doc):
    u"""Обычные спецификации проекта (без шаблонов, ключевых и штамповых)."""
    out = []
    for v in FilteredElementCollector(doc).OfClass(ViewSchedule):
        try:
            if v.IsTemplate:
                continue
            d = v.Definition
            if d is None or d.IsKeySchedule:
                continue
            if getattr(v, "IsTitleblockRevisionSchedule", False):
                continue
        except Exception:
            continue
        out.append(v)
    out.sort(key=schedule_name)
    return out


def _visible_fields(sched):
    d = sched.Definition
    fields = []
    for i in range(d.GetFieldCount()):
        f = d.GetField(i)
        try:
            if f.IsHidden:
                continue
        except Exception:
            pass
        fields.append(f)
    return fields


def _param_to_text(p):
    if p is None or not p.HasValue:
        return u""
    st = p.StorageType
    try:
        if st == StorageType.String:
            return p.AsString() or u""
        if st == StorageType.Integer:
            vs = p.AsValueString()
            return vs if vs is not None else unicode(p.AsInteger())
        if st == StorageType.Double:
            vs = p.AsValueString()
            return vs if vs is not None else unicode(p.AsDouble())
        if st == StorageType.ElementId:
            vs = p.AsValueString()
            if vs:
                return vs
            eid = p.AsElementId()
            return unicode(eid.IntegerValue) if eid is not None else u""
    except Exception:
        return u""
    return u""


def _set_param_text(p, text):
    st = p.StorageType
    try:
        if st == StorageType.String:
            return bool(p.Set(text))
        if st == StorageType.Double:
            if p.SetValueString(text):
                return True
            alt = text.replace(u".", u",") if u"." in text else text.replace(u",", u".")
            if alt != text and p.SetValueString(alt):
                return True
            return bool(p.Set(float(text.replace(u",", u"."))))
        if st == StorageType.Integer:
            t = text.strip().lower()
            if t in (u"да", u"yes", u"true", u"истина", u"1", u"x", u"✓"):
                return bool(p.Set(1))
            if t in (u"нет", u"no", u"false", u"ложь", u"0", u"-", u""):
                return bool(p.Set(0))
            return bool(p.Set(int(round(float(text.replace(u",", u"."))))))
    except Exception:
        return False
    return False


def _natural_key(s):
    u"""Ключ «естественной» сортировки: «Поз. 2» < «Поз. 10» (как в Revit)."""
    parts = _DIGITS.split(s or u"")
    key = []
    for i, t in enumerate(parts):
        if i % 2:
            key.append((0, int(t), u""))
        elif t:
            key.append((1, 0, t.lower()))
    return key


def _sort_value(el, name):
    u"""Значение параметра элемента для сортировки, повторяющей спецификацию."""
    p = el.LookupParameter(name)
    if p is None or not p.HasValue:
        return (2, 0.0, [])          # пустые — в конец
    st = p.StorageType
    try:
        if st == StorageType.Double:
            return (0, p.AsDouble(), [])
        if st == StorageType.Integer:
            return (0, float(p.AsInteger()), [])
        if st == StorageType.String:
            return (1, 0.0, _natural_key(p.AsString()))
        if st == StorageType.ElementId:
            return (1, 0.0, _natural_key(p.AsValueString()))
    except Exception:
        pass
    return (2, 0.0, [])


def _ordered_elements(doc, sched):
    u"""
    Элементы спецификации в том же порядке, что и её строки: повторяем
    поля сортировки/группировки (`GetSortGroupField`). Группировка в Revit —
    это та же сортировка по полю группы в первую очередь.
    """
    els = list(FilteredElementCollector(doc, sched.Id)
               .WhereElementIsNotElementType()
               .ToElements())

    d = sched.Definition
    try:
        cnt = d.GetSortGroupFieldCount()
    except Exception:
        cnt = 0

    keys = []
    for i in range(cnt):
        try:
            sgf = d.GetSortGroupField(i)
            name = d.GetField(sgf.FieldId).GetName()
            ascending = (sgf.SortOrder == ScheduleSortOrder.Ascending)
            keys.append((name, ascending))
        except Exception:
            pass

    # многоключевая устойчивая сортировка: от последнего поля к первому
    for name, ascending in reversed(keys):
        els.sort(key=lambda e, nm=name: _sort_value(e, nm), reverse=not ascending)

    return els


def _instance_param_map(el):
    u"""{имя параметра экземпляра: Parameter} за один проход — вместо
    LookupParameter на каждый столбец (тот каждый раз линейно сканирует
    все параметры элемента)."""
    m = {}
    for p in el.Parameters:
        try:
            m.setdefault(p.Definition.Name, p)
        except Exception:
            pass
    return m


def schedule_to_rows(doc, sched):
    u"""
    (rows, число_элементов, число_столбцов-параметров, ширины_столбцов).
    rows[0] — заголовок; ширины — в «символах» Excel, по столбцу на каждый
    (первый — под «Revit ID», дальше — из ширин граф спецификации).
    """
    fields = _visible_fields(sched)
    names = [f.GetName() for f in fields]

    widths = [_ID_COL_WIDTH]
    for f in fields:
        try:
            widths.append(_excel_col_width(f.GridColumnWidth))
        except Exception:
            widths.append(0.0)

    els = _ordered_elements(doc, sched)

    rows = [[ID_HEADER] + names]
    for el in els:
        pmap = _instance_param_map(el)
        row = [el.Id.IntegerValue]
        for nm in names:
            row.append(_param_to_text(pmap.get(nm)))
        rows.append(row)

    return rows, len(els), len(names), widths


def _cell_int(row, idx):
    if idx is None or idx >= len(row) or row[idx] is None:
        return None
    s = unicode(row[idx]).strip().replace(u",", u".")
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def merge_export(new_rows, new_widths, existing_rows, existing_widths=None):
    u"""
    Совместить свежую выгрузку с уже существующим файлом: значения полей
    спецификации обновляются, а добавленные пользователем столбцы (и их
    ширины) и ручные строки (итоги/примечания без Revit ID) сохраняются.

    Возвращает (rows, widths, stats). Если existing_rows не похож на наш
    файл (нет столбца Revit ID) — вернёт свежую выгрузку без изменений.
    """
    existing_widths = existing_widths or {}
    stats = {"matched": 0, "vanished": 0, "manual": 0, "added_cols": 0, "merged": False}

    new_header = [unicode(c) if c is not None else u"" for c in new_rows[0]]

    hidx = None
    for i, r in enumerate(existing_rows):
        if r and any(c is not None and unicode(c).strip() for c in r):
            hidx = i
            break
    if hidx is None:
        return new_rows, new_widths, stats

    ex_header = [unicode(c).strip() if c is not None else u"" for c in existing_rows[hidx]]
    ex_id_col = None
    for i, h in enumerate(ex_header):
        if h.lower() in _ID_ALIASES:
            ex_id_col = i
            break
    if ex_id_col is None:
        return new_rows, new_widths, stats

    stats["merged"] = True

    newname_to_idx = {}
    for j, h in enumerate(new_header):
        newname_to_idx.setdefault(h.strip().lower(), j)

    ex_names_lower = [h.strip().lower() for h in ex_header]

    # объединённый заголовок: существующий как есть + новые поля спеки в хвост
    merged_header = list(ex_header)
    appended_src = []
    for j, h in enumerate(new_header):
        if j == 0:
            continue  # Revit ID уже есть (ex_id_col)
        if h.strip().lower() not in ex_names_lower:
            merged_header.append(h)
            appended_src.append(j)
    stats["added_cols"] = len(appended_src)

    # план заполнения каждого столбца объединённого заголовка
    plan = []
    for k, h in enumerate(merged_header):
        if k == ex_id_col:
            plan.append(("id", 0))
        else:
            j = newname_to_idx.get(h.strip().lower())
            plan.append(("revit", j) if j is not None else ("user", k))

    # существующие строки: с валидным Revit ID -> в карту, прочие -> ручные
    ex_by_id = {}
    manual_rows = []
    for r in existing_rows[hidx + 1:]:
        rid = _cell_int(r, ex_id_col)
        if rid is None:
            if any(c is not None and unicode(c).strip() for c in r):
                manual_rows.append(r)
        else:
            ex_by_id[rid] = r

    def _pick(row, idx):
        return row[idx] if (idx is not None and idx < len(row)) else u""

    out = [merged_header]
    seen = set()
    for nr in new_rows[1:]:
        nid = _cell_int(nr, 0)
        er = ex_by_id.get(nid)
        if er is not None:
            stats["matched"] += 1
            seen.add(nid)
        row = []
        for kind, idx in plan:
            if kind == "id":
                row.append(_pick(nr, 0))
            elif kind == "revit":
                row.append(_pick(nr, idx))
            else:  # user — из существующей строки, без изменений
                row.append(_pick(er, idx) if er is not None else u"")
        out.append(row)

    # хвост: ручные строки и строки исчезнувших элементов — как есть
    leftover = list(manual_rows)
    for rid, er in ex_by_id.items():
        if rid not in seen:
            stats["vanished"] += 1
            leftover.append(er)
    stats["manual"] = len(manual_rows)
    for er in leftover:
        out.append([_pick(er, k) for k in range(len(merged_header))])

    # ширины: у существующих столбцов — из файла (или ширина поля спеки,
    # если в файле не было), у дописанных — ширина поля спеки
    widths = []
    for k in range(len(ex_header)):
        w = existing_widths.get(k)
        if not w:
            j = newname_to_idx.get(ex_names_lower[k])
            if j is not None and j < len(new_widths):
                w = new_widths[j]
        widths.append(w or 0.0)
    for pos, j in enumerate(appended_src):
        widths.append(new_widths[j] if j < len(new_widths) else 0.0)

    return out, widths, stats


def rows_to_model(doc, rows):
    u"""
    Применить правки из rows к модели. Вызывать внутри транзакции.
    Возвращает dict со счётчиками и списком ошибок.
    """
    res = {
        "changed": 0, "unchanged": 0, "no_element": 0,
        "no_param": 0, "read_only": 0, "errors": [],
    }
    if not rows:
        return res

    # первая непустая строка — заголовок
    hidx = None
    for i, r in enumerate(rows):
        if r and any(c is not None and unicode(c).strip() for c in r):
            hidx = i
            break
    if hidx is None:
        return res

    header = [unicode(c).strip() if c is not None else u"" for c in rows[hidx]]
    id_col = None
    for i, h in enumerate(header):
        if h.lower() in _ID_ALIASES:
            id_col = i
            break
    if id_col is None:
        res["errors"].append(u"Не найден столбец «{}».".format(ID_HEADER))
        return res

    # столбцы-параметры считаем один раз
    cols = [(ci, h) for ci, h in enumerate(header) if h and ci != id_col]

    changed = unchanged = no_element = no_param = read_only = 0
    errors = res["errors"]
    get_el = doc.GetElement
    eid_type = StorageType.ElementId

    for r in rows[hidx + 1:]:
        n = len(r)
        if id_col >= n or r[id_col] is None:
            continue
        raw_id = unicode(r[id_col]).strip()
        if not raw_id:
            continue
        try:
            eid = int(float(raw_id.replace(u",", u".")))
        except ValueError:
            continue

        el = get_el(ElementId(eid))
        if el is None:
            no_element += 1
            continue

        # заполненные ячейки этой строки
        pending = []
        for ci, h in cols:
            if ci < n:
                cell = r[ci]
                if cell is not None:
                    txt = unicode(cell)
                    if txt.strip():
                        pending.append((h, txt))
        if not pending:
            continue

        pmap = _instance_param_map(el)

        for h, new_text in pending:
            p = pmap.get(h)
            if p is None:
                no_param += 1
                continue
            if p.IsReadOnly or p.StorageType == eid_type:
                read_only += 1
                continue
            if _param_to_text(p).strip() == new_text.strip():
                unchanged += 1
                continue
            if _set_param_text(p, new_text):
                changed += 1
            else:
                errors.append(
                    u"ID {} / «{}»: не удалось записать «{}»".format(eid, h, new_text)
                )

    res["changed"] = changed
    res["unchanged"] = unchanged
    res["no_element"] = no_element
    res["no_param"] = no_param
    res["read_only"] = read_only
    return res
