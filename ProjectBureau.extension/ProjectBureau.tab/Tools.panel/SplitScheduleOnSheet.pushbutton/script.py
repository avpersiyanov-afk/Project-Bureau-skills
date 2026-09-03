# -*- coding: utf-8 -*-

__title__ = u"Разбить\nспецификацию"
__doc__ = u"Разбивает выбранную спецификацию на листе на участки и раскладывает их в ряд"
__author__ = "Pipers"

import math
import traceback

import clr
clr.AddReference('RevitAPI')
clr.AddReference('RevitAPIUI')
clr.AddReference('Microsoft.VisualBasic')

from System.Collections.Generic import List
from Microsoft.VisualBasic import Interaction
from Autodesk.Revit.DB import (
    ElementTransformUtils,
    FilteredElementCollector,
    ScheduleSheetInstance,
    SectionType,
    Transaction,
    ViewSchedule,
    XYZ,
)
from pyrevit import revit, forms


doc = revit.doc
uidoc = revit.uidoc

MM_IN_FOOT = 304.8

GAP_MM = 5.0
FALLBACK_STEP_MM = 300.0
# Запас, чтобы участок гарантированно не вылезал за заданную высоту
SAFETY_MM = 3.0
MAX_SEGMENTS = 60

_debug = []


class Cancelled(Exception):
    pass


class Stop(Exception):
    pass


def dbg(msg):
    _debug.append(unicode(msg))


def is_num(x):
    return x is not None and not math.isinf(x) and not math.isnan(x)


def get_target_schedule():
    schedule_ids = set()
    picked = None
    picked_inst = None
    for el_id in uidoc.Selection.GetElementIds():
        el = doc.GetElement(el_id)
        if isinstance(el, ScheduleSheetInstance):
            sched = doc.GetElement(el.ScheduleId)
            if isinstance(sched, ViewSchedule):
                schedule_ids.add(sched.Id.IntegerValue)
                picked = sched
                picked_inst = el
        elif isinstance(el, ViewSchedule):
            schedule_ids.add(el.Id.IntegerValue)
            picked = el
    return picked, picked_inst, len(schedule_ids)


def ask(prompt, default):
    answer = Interaction.InputBox(prompt, u"Разбить спецификацию", unicode(default))
    if answer is None or not answer.strip():
        raise Cancelled()
    return answer


def to_float_mm(raw):
    raw = raw.strip().lower().replace(",", ".")
    for suffix in (u"мм", u"mm", u"м"):
        if raw.endswith(suffix):
            raw = raw[:-len(suffix)].strip()
            break
    try:
        v = float(raw)
    except ValueError:
        raise Stop(u"Не понял число: «{}».".format(raw))
    if v <= 0:
        raise Stop(u"Значение должно быть больше нуля.")
    return v


def parse_request(raw):
    u"""('count', N) — N равных участков; ('mm', высота_фт) — по высоте."""
    low = raw.strip().lower()
    is_mm = any(low.endswith(s) for s in (u"мм", u"mm", u"м"))
    value_mm = to_float_mm(raw)
    if is_mm:
        return "mm", value_mm / MM_IN_FOOT
    count = int(round(value_mm))
    if count < 2:
        raise Stop(u"Участков должно быть хотя бы 2.")
    if count > MAX_SEGMENTS:
        raise Stop(u"{} участков — слишком много.".format(count))
    return "count", count


def probe_body_ft(sched):
    u"""
    Оценка высоты ТЕЛА спеки (строки данных), футы. 0.0 — не вышло.
    Split(2) во временной транзакции -> GetSegmentHeight(0) (это предел тела
    первой из двух равных частей) -> *2 -> откат. Проба стабильно занижает
    результат примерно на высоту одной шапки — это учитывается снаружи.
    """
    h0 = None
    t = Transaction(doc, u"Замер спецификации")
    t.Start()
    try:
        sched.Split(2)
        doc.Regenerate()
        h0 = sched.GetSegmentHeight(0)
    except Exception as ex:
        dbg(u"проба Split(2): {}".format(ex))
    finally:
        try:
            t.RollBack()
        except Exception as ex:
            dbg(u"откат пробы: {}".format(ex))
    total = 2.0 * h0 if (is_num(h0) and h0 > 0) else 0.0
    dbg(u"проба тела: {:.0f} мм (GetSegmentHeight(0)={})".format(
        total * MM_IN_FOOT, h0))
    return total


def header_height_ft(sched):
    u"""Высота повторяющейся шапки (заголовок + названия граф), футы, из модели
    таблицы. 0.0 — не удалось."""
    try:
        td = sched.GetTableData()
    except Exception as ex:
        dbg(u"GetTableData: {}".format(ex))
        return 0.0

    try:
        sd = td.GetSectionData(SectionType.Header)
    except Exception as ex:
        dbg(u"GetSectionData(Header): {}".format(ex))
        return 0.0
    if sd is None:
        return 0.0

    total = 0.0
    rows = 0
    try:
        for r in range(sd.FirstRowNumber, sd.LastRowNumber + 1):
            try:
                total += sd.GetRowHeight(r)
                rows += 1
            except Exception:
                pass
    except Exception as ex:
        dbg(u"строки шапки: {}".format(ex))
    dbg(u"шапка: строк {}, высота {:.0f} мм".format(rows, total * MM_IN_FOOT))
    return total if (is_num(total) and total > 0) else 0.0


def unsplit(sched):
    u"""Собрать уже разбитую спеку в одну — перебором вероятных сигнатур."""
    guard = 0
    while sched.GetSegmentCount() > 1 and guard < 200:
        n = sched.GetSegmentCount()
        moved = False
        for call in (
            lambda: sched.MergeSegments(0),
            lambda: sched.MergeSegments(),
            lambda: sched.MergeSegments(0, 1),
            lambda: sched.DeleteSegment(n - 1),
        ):
            try:
                call()
                moved = True
                break
            except Exception as ex:
                dbg(u"unsplit#{}: {}".format(guard, ex))
        if not moved or sched.GetSegmentCount() >= n:
            break
        guard += 1


def instance_width_ft(inst):
    u"""Ширина размещённого экземпляра спецификации на листе, футы (до разбиения
    габарит по X достоверный, в отличие от габарита по Y)."""
    sheet = doc.GetElement(inst.OwnerViewId)
    try:
        bb = inst.get_BoundingBox(sheet)
    except Exception as ex:
        dbg(u"bbox ширины: {}".format(ex))
        bb = None
    if bb is None:
        return 0.0
    w = bb.Max.X - bb.Min.X
    return w if (is_num(w) and w > 0) else 0.0


def arrange_in_row(sched, sheet_id, origin, width_ft, count, original_id):
    doc.Regenerate()
    if is_num(width_ft) and width_ft > 0:
        step = width_ft + GAP_MM / MM_IN_FOOT
    else:
        step = FALLBACK_STEP_MM / MM_IN_FOOT

    # Собрать экземпляры этой спеки на этом листе; первый на каждый валидный
    # индекс сегмента оставляем, всё остальное (старая цельная спека до
    # разбиения, дубли, индексы вне диапазона) — под снос. Исходный экземпляр
    # сносим всегда: если Revit оставил его отдельным — уберём, если сделал
    # сегментом 0 — пересоздадим ниже на нужном месте.
    by_seg = {}
    strays = []
    for inst in FilteredElementCollector(doc).OfClass(ScheduleSheetInstance):
        if inst.ScheduleId.IntegerValue != sched.Id.IntegerValue:
            continue
        if inst.OwnerViewId.IntegerValue != sheet_id.IntegerValue:
            continue
        si = inst.SegmentIndex
        if (original_id is not None
                and inst.Id.IntegerValue == original_id.IntegerValue):
            strays.append(inst.Id)
        elif 0 <= si < count and si not in by_seg:
            by_seg[si] = inst
        else:
            strays.append(inst.Id)

    removed = 0
    for sid in strays:
        try:
            doc.Delete(sid)
            removed += 1
        except Exception as ex:
            dbg(u"Delete лишнего: {}".format(ex))
    if removed:
        doc.Regenerate()
    dbg(u"удалено лишних экземпляров: {}".format(removed))

    created = 0
    moved = 0
    for k in range(count):
        target = XYZ(origin.X + step * k, origin.Y, origin.Z)
        inst = by_seg.get(k)
        if inst is None:
            try:
                ScheduleSheetInstance.Create(doc, sheet_id, sched.Id, target, k)
                created += 1
            except Exception as ex:
                dbg(u"Create сегм.{}: {}".format(k, ex))
            continue
        try:
            delta = target - inst.Point
            if delta.GetLength() > 1e-7:
                ElementTransformUtils.MoveElement(doc, inst.Id, delta)
                moved += 1
        except Exception as ex:
            dbg(u"Move сегм.{}: {}".format(k, ex))
    return created, moved, removed


def main():
    sched, sched_inst, distinct = get_target_schedule()

    if sched is None:
        raise Stop(u"Сначала выберите на листе спецификацию, потом запустите кнопку.")
    if distinct > 1:
        raise Stop(u"Выбрано несколько разных спецификаций. Оставьте одну.")
    if getattr(sched, "IsTitleblockRevisionSchedule", False):
        raise Stop(u"Спецификацию изменений в штампе разбить нельзя.")
    if sched_inst is None:
        raise Stop(
            u"Выберите экземпляр спецификации на листе (щёлкните по таблице "
            u"на листе), а не спецификацию в диспетчере проекта."
        )

    try:
        already_split = sched.IsSplit()
    except AttributeError:
        raise Stop(u"Эта сборка Revit не поддерживает разбиение спецификаций через API.")

    if already_split:
        if not forms.alert(
            u"Спецификация уже разбита на {} участков. Собрать обратно "
            u"и разбить заново?".format(sched.GetSegmentCount()),
            yes=True, no=True
        ):
            raise Cancelled()

    raw = ask(
        u"На сколько участков разбить спецификацию? (например  3)\n"
        u"Либо высота одного участка: число с «мм» (например  180мм).",
        3
    )
    mode, amount = parse_request(raw)

    sheet_id = sched_inst.OwnerViewId
    origin = sched_inst.Point
    original_id = sched_inst.Id
    width_ft = instance_width_ft(sched_inst)

    body_each_ft = amount

    if mode == "count":
        count = amount
    else:
        header_ft = header_height_ft(sched)
        if header_ft <= 0:
            r3 = ask(
                u"Не смог определить высоту повторяющейся шапки спецификации.\n"
                u"Введите её в мм (заголовок + строка названий граф; 0 — не "
                u"учитывать):",
                0
            ).strip().lower().replace(",", ".")
            try:
                header_ft = max(0.0, float(r3)) / MM_IN_FOOT
            except ValueError:
                header_ft = 0.0
            dbg(u"шапка вручную: {:.0f} мм".format(header_ft * MM_IN_FOOT))

        body_target_ft = amount - header_ft
        if body_target_ft <= 0:
            raise Stop(
                u"Высота участка {:.0f} мм не больше шапки таблицы (~{:.0f} мм). "
                u"Задайте больше.".format(amount * MM_IN_FOOT, header_ft * MM_IN_FOOT)
            )

        probe = 0.0 if already_split else probe_body_ft(sched)
        if probe > 0:
            # проба занижает примерно на одну шапку — компенсируем
            total_body_ft = probe + header_ft
        else:
            rv = ask(
                u"Высоту не удалось измерить.\n"
                u"Введите полную высоту всей спецификации на листе в мм:",
                2000
            )
            total_body_ft = max(0.0, to_float_mm(rv) / MM_IN_FOOT - header_ft)
            dbg(u"высота вручную: тело {:.0f} мм".format(total_body_ft * MM_IN_FOOT))

        if total_body_ft <= 0:
            raise Stop(u"Не удалось определить высоту спецификации.")

        eff = body_target_ft - SAFETY_MM / MM_IN_FOOT
        if eff <= 0:
            eff = body_target_ft
        count = max(2, int(math.ceil(total_body_ft / eff - 1e-9)))
        if count >= MAX_SEGMENTS:
            raise Stop(
                u"Получается слишком много участков ({}+). Увеличьте высоту "
                u"участка.".format(MAX_SEGMENTS)
            )
        # равные по телу участки — без «хвоста» из одной шапки
        body_each_ft = total_body_ft / count

    with revit.Transaction(u"Разбить спецификацию на листе"):
        if sched.GetSegmentCount() > 1:
            unsplit(sched)
            doc.Regenerate()
            if sched.GetSegmentCount() > 1:
                raise Stop(
                    u"Не удалось собрать спецификацию в одну (сейчас {} "
                    u"участков). Соберите вручную и повторите.\n\n{}".format(
                        sched.GetSegmentCount(), u"\n".join(_debug)
                    )
                )

        if mode == "mm":
            heights = List[float]()
            for _ in range(count - 1):
                heights.Add(body_each_ft)
            sched.Split(heights)
        else:
            sched.Split(count)

        doc.Regenerate()
        arrange_in_row(sched, sheet_id, origin, width_ft, count, original_id)

    # Успех — без итогового окна. Сообщения показываем только при отмене/сбое.


try:
    main()
except Cancelled:
    pass
except Stop as ex:
    forms.alert(unicode(ex))
except Exception:
    tail = (u"\n\nДиагностика:\n" + u"\n".join(_debug)) if _debug else u""
    forms.alert(
        u"Сбой при разбиении спецификации:\n\n{}{}".format(
            traceback.format_exc(), tail),
        title=u"Разбить спецификацию"
    )
