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

# Шаг ряда: следующий участок начинается через столько мм ОТ НАЧАЛА
# предыдущего (не от его правого края) — фиксированное значение, не зависит
# от реальной ширины таблицы (в т.ч. от скрытых столбцов).
COLUMN_PITCH_MM = 420.0
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


def parse_height_ft(raw):
    u"""Запрошенная высота одного участка на листе, футы. Число в мм, суффикс
    «мм» необязателен."""
    return to_float_mm(raw) / MM_IN_FOOT


def body_rows_total_ft(sched):
    u"""Сумма высот строк секции Body из модели таблицы, футы (без шапки).
    Не учитывает перенос текста в ячейках — используется только как самый
    последний запасной вариант. 0.0 — не вышло."""
    try:
        td = sched.GetTableData()
        sd = td.GetSectionData(SectionType.Body)
    except Exception as ex:
        dbg(u"GetSectionData(Body): {}".format(ex))
        return 0.0
    if sd is None:
        return 0.0
    total = 0.0
    rows = 0
    try:
        for r in range(sd.FirstRowNumber, sd.LastRowNumber + 1):
            try:
                h = sd.GetRowHeight(r)
                if is_num(h) and h > 0:
                    total += h
                    rows += 1
            except Exception:
                pass
    except Exception as ex:
        dbg(u"строки тела: {}".format(ex))
    dbg(u"сумма строк тела: {:.1f} мм ({} строк)".format(total * MM_IN_FOOT, rows))
    return total if (is_num(total) and total > 0) else 0.0


def probe_total_border_ft(sched):
    u"""
    Оценка ПОЛНОЙ высоты спецификации (шапка + всё тело), футы. 0.0 — не
    вышло. Split(2) во временной транзакции (RollBack, без последствий) ->
    GetSegmentHeight(0). По документации RevitAPI (Split(int)/GetSegmentHeight):
    возвращаемое значение — это ГРАНИЦА сегмента (шапка+тело), и Split(int)
    делит именно эту полную высоту поровну. Значит для Split(2):
    GetSegmentHeight(0) = ПолнаяВысота / 2 -> ПолнаяВысота = 2 * GetSegmentHeight(0).
    Это та же величина, что ожидает SetSegmentHeight, поэтому дальше не нужно
    отдельно прибавлять/вычитать шапку.
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
    dbg(u"проба полной высоты: {:.0f} мм (GetSegmentHeight(0)={})".format(
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


def arrange_in_row(sched, sheet_id, origin, count, original_id):
    doc.Regenerate()
    step = COLUMN_PITCH_MM / MM_IN_FOOT

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
        u"Высота одного участка спецификации на листе, мм (например  180).",
        180
    )
    amount = parse_height_ft(raw)

    sheet_id = sched_inst.OwnerViewId
    origin = sched_inst.Point
    original_id = sched_inst.Id

    header_ft = header_height_ft(sched)
    detected_mm = header_ft * MM_IN_FOOT
    r3 = ask(
        u"Высота повторяющейся шапки спецификации в мм "
        u"(заголовок + строка названий граф).\n"
        u"{}\n"
        u"Исправьте, если определилось неверно; 0 — не учитывать:".format(
            u"Определено автоматически: {:.0f} мм.".format(detected_mm)
            if header_ft > 0 else u"Определить автоматически не удалось."
        ),
        int(round(detected_mm)) if header_ft > 0 else 0
    ).strip().lower().replace(",", ".")
    try:
        header_ft = max(0.0, float(r3)) / MM_IN_FOOT
    except ValueError:
        pass
    dbg(u"шапка итог: {:.0f} мм".format(header_ft * MM_IN_FOOT))

    if amount <= header_ft:
        raise Stop(
            u"Высота участка {:.0f} мм не больше шапки таблицы (~{:.0f} мм). "
            u"Задайте больше.".format(amount * MM_IN_FOOT, header_ft * MM_IN_FOOT)
        )

    # Полная высота спецификации (шапка + всё тело) — та же величина, что
    # ожидает SetSegmentHeight. Проба Split(2) читает уже рассчитанную
    # Revit'ом раскладку; сумма строк секции Body (+ шапка) и ручной ввод —
    # запасные варианты. Автоопределение здесь и раньше ошибалось, поэтому
    # значение ВСЕГДА показывается для проверки/правки, как высота шапки.
    guess_ft = 0.0 if already_split else probe_total_border_ft(sched)
    guess_src = u"проба Split(2)"
    if guess_ft <= 0:
        rows_ft = body_rows_total_ft(sched)
        if rows_ft > 0:
            guess_ft = rows_ft + header_ft
            guess_src = u"сумма строк + шапка"
    guess_mm = guess_ft * MM_IN_FOOT
    rv = ask(
        u"Полная высота спецификации на листе — шапка + все строки, мм.\n"
        u"{}\n"
        u"Проверьте по факту (например, в свойствах вида) и исправьте, если "
        u"не совпадает:".format(
            u"Определено автоматически: {:.0f} мм ({}).".format(guess_mm, guess_src)
            if guess_ft > 0 else u"Определить автоматически не удалось."
        ),
        int(round(guess_mm)) if guess_ft > 0 else 2000
    )
    total_ft = to_float_mm(rv) / MM_IN_FOOT
    dbg(u"полная высота итог: {:.0f} мм".format(total_ft * MM_IN_FOOT))

    # N участков: столько, чтобы полная высота уместилась по border=amount
    # у первых N-1 (столько, сколько нарисуется целых строк), последний
    # добирает остаток.
    count = max(2, int(math.ceil(total_ft / amount - 0.02)))
    if count >= MAX_SEGMENTS:
        raise Stop(
            u"Получается слишком много участков ({}+). Увеличьте высоту "
            u"участка.".format(MAX_SEGMENTS)
        )

    if not forms.alert(
        u"Участков: {}\n"
        u"Шапка (на каждом участке): {:.0f} мм\n"
        u"Полная высота таблицы: {:.0f} мм\n"
        u"Первые {} участка(ов) — по {:.0f} мм (насколько позволит целое "
        u"число строк), последний — остаток.\n\n"
        u"Разбить?".format(
            count,
            header_ft * MM_IN_FOOT,
            total_ft * MM_IN_FOOT,
            count - 1,
            amount * MM_IN_FOOT,
        ),
        yes=True, no=True
    ):
        raise Cancelled()

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

        sched.Split(count)
        doc.Regenerate()
        # Split(int) режет на равные части; принудительно задаём границу каждого
        # участка (кроме последнего) под запрошенную высоту. SetSegmentHeight —
        # это ГРАНИЦА (шапка + тело); реальная высота на листе <= неё и кратна
        # строкам, последний участок вбирает остаток.
        pinned = 0
        for i in range(count - 1):
            try:
                sched.SetSegmentHeight(i, amount)
                pinned += 1
            except Exception as ex:
                dbg(u"SetSegmentHeight({}): {}".format(i, ex))
        if pinned:
            doc.Regenerate()
        dbg(u"границы участков заданы: {}/{}".format(pinned, count - 1))

        arrange_in_row(sched, sheet_id, origin, count, original_id)

    if count > 2 and pinned == 0:
        forms.alert(
            u"Высоту участков задать через API не удалось — Revit разбил на {} "
            u"равные части. Проверьте результат.\n\n{}".format(
                count, u"\n".join(_debug)
            ),
            title=u"Разбить спецификацию"
        )
    # Иначе успех — без итогового окна.


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
