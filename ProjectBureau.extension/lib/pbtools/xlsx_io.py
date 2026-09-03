# -*- coding: utf-8 -*-
"""
Мини чтение/запись .xlsx без сторонних пакетов (openpyxl не нужен).

Работает на IronPython 2 через System.IO.Compression: .xlsx — это zip с
XML внутри. Пишем строки как inlineStr, читаем и inlineStr, и обычные
sharedStrings (так Excel пересохраняет файл после правки).

Ограничения намеренные: один лист, без стилей/форматов/формул. Значения
ячеек на чтении возвращаются как unicode или None. Этого хватает для
сценария «выгрузил параметры — поправил в Excel — загрузил обратно».
"""

import re

import clr
clr.AddReference("System.IO.Compression")
try:
    clr.AddReference("System.IO.Compression.FileSystem")
except Exception:
    pass
try:
    clr.AddReference("System.Xml")
except Exception:
    pass

from System.IO import FileStream, FileMode, FileAccess, StreamReader, StringReader
from System.IO.Compression import ZipArchive, ZipArchiveMode
from System.Text import Encoding
from System.Xml import XmlReader, XmlReaderSettings, XmlNodeType


_CT = (
    u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    u'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    u'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    u'<Default Extension="xml" ContentType="application/xml"/>'
    u'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
    u'<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
    u'</Types>'
)

_RELS = (
    u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    u'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    u'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
    u'</Relationships>'
)

_WB = (
    u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    u'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
    u'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    u'<sheets><sheet name="{name}" sheetId="1" r:id="rId1"/></sheets></workbook>'
)

_WB_RELS = (
    u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    u'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    u'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
    u'</Relationships>'
)


def _col_letter(idx):
    u"""0 -> A, 25 -> Z, 26 -> AA."""
    s = u""
    n = idx + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = unichr(65 + r) + s
    return s


def _col_index(letters):
    u"""A -> 0, AA -> 26."""
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _esc(s):
    return (s.replace(u"&", u"&amp;").replace(u"<", u"&lt;")
            .replace(u">", u"&gt;").replace(u'"', u"&quot;"))


def _unesc(s):
    return (s.replace(u"&lt;", u"<").replace(u"&gt;", u">")
            .replace(u"&quot;", u'"').replace(u"&apos;", u"'")
            .replace(u"&#10;", u"\n").replace(u"&#13;", u"\r")
            .replace(u"&#9;", u"\t").replace(u"&amp;", u"&"))


def _sheet_xml(rows, col_widths=None):
    out = [
        u'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        u'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
    ]
    if col_widths:
        cols = []
        for i, w in enumerate(col_widths):
            try:
                w = float(w)
            except (TypeError, ValueError):
                continue
            if w > 0:
                cols.append(
                    u'<col min="%d" max="%d" width="%.2f" customWidth="1"/>'
                    % (i + 1, i + 1, w)
                )
        if cols:
            out.append(u'<cols>' + u"".join(cols) + u'</cols>')
    out.append(u'<sheetData>')
    for ri, row in enumerate(rows):
        out.append(u'<row r="%d">' % (ri + 1))
        for ci, val in enumerate(row):
            if val is None or val == u"":
                continue
            ref = u"%s%d" % (_col_letter(ci), ri + 1)
            if isinstance(val, bool):
                val = u"1" if val else u"0"
            if isinstance(val, (int, long, float)):
                out.append(u'<c r="%s"><v>%s</v></c>' % (ref, unicode(val)))
            else:
                out.append(
                    u'<c r="%s" t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>'
                    % (ref, _esc(unicode(val)))
                )
        out.append(u'</row>')
    out.append(u'</sheetData></worksheet>')
    return u"".join(out)


def write_xlsx(path, rows, sheet_name=u"Лист1", col_widths=None):
    u"""
    rows — список списков (str/число/None). Первая строка обычно заголовок.
    col_widths — ширины столбцов в «символах» Excel (как в диалоге ширины
    столбца), по одному значению на столбец; None/0 — ширина по умолчанию.
    """
    parts = [
        (u"[Content_Types].xml", _CT),
        (u"_rels/.rels", _RELS),
        (u"xl/workbook.xml", _WB.format(name=_esc(sheet_name[:31]))),
        (u"xl/_rels/workbook.xml.rels", _WB_RELS),
        (u"xl/worksheets/sheet1.xml", _sheet_xml(rows, col_widths)),
    ]
    fs = FileStream(path, FileMode.Create)
    try:
        zf = ZipArchive(fs, ZipArchiveMode.Create)
        try:
            for name, text in parts:
                entry = zf.CreateEntry(name)
                st = entry.Open()
                try:
                    data = Encoding.UTF8.GetBytes(text)
                    st.Write(data, 0, data.Length)
                finally:
                    st.Close()
        finally:
            zf.Dispose()
    finally:
        fs.Close()


def _read_entry(zf, name):
    e = zf.GetEntry(name)
    if e is None:
        return None
    st = e.Open()
    try:
        sr = StreamReader(st, Encoding.UTF8)
        try:
            return sr.ReadToEnd()
        finally:
            sr.Close()
    finally:
        st.Close()


_TEXT_NODES = (
    XmlNodeType.Text, XmlNodeType.CDATA,
    XmlNodeType.SignificantWhitespace, XmlNodeType.Whitespace,
)


def _reader(xml):
    s = XmlReaderSettings()
    s.IgnoreComments = True
    s.IgnoreProcessingInstructions = True
    s.CheckCharacters = False
    return XmlReader.Create(StringReader(xml), s)


def _shared_strings(zf):
    txt = _read_entry(zf, u"xl/sharedStrings.xml")
    if not txt:
        return []
    items = []
    buf = []
    in_t = False
    rdr = _reader(txt)
    try:
        while rdr.Read():
            nt = rdr.NodeType
            if nt == XmlNodeType.Element:
                ln = rdr.LocalName
                if ln == u"si":
                    buf = []
                elif ln == u"t":
                    in_t = True
            elif nt in _TEXT_NODES:
                if in_t:
                    buf.append(rdr.Value)
            elif nt == XmlNodeType.EndElement:
                ln = rdr.LocalName
                if ln == u"t":
                    in_t = False
                elif ln == u"si":
                    items.append(u"".join(buf))
    finally:
        rdr.Close()
    return items


def _parse_sheet(xml, shared):
    rows = []
    cells = {}
    maxc = -1
    col = -1
    ctype = None
    in_v = False
    in_t = False
    vbuf = []
    tbuf = []

    rdr = _reader(xml)
    try:
        while rdr.Read():
            nt = rdr.NodeType

            if nt == XmlNodeType.Element:
                ln = rdr.LocalName
                if ln == u"c":
                    ref = rdr.GetAttribute(u"r")
                    ctype = rdr.GetAttribute(u"t")
                    col = _col_index(_letters(ref)) if ref else (maxc + 1)
                    if rdr.IsEmptyElement and col > maxc:
                        maxc = col
                elif ln == u"v":
                    if rdr.IsEmptyElement:
                        cells[col] = None if ctype == u"s" else u""
                        if col > maxc:
                            maxc = col
                    else:
                        in_v = True
                        vbuf = []
                elif ln == u"t":
                    if rdr.IsEmptyElement:
                        if col not in cells or cells[col] is None:
                            cells[col] = u""
                        if col > maxc:
                            maxc = col
                    else:
                        in_t = True
                        tbuf = []
                elif ln == u"row":
                    cells = {}
                    maxc = -1

            elif nt in _TEXT_NODES:
                if in_v:
                    vbuf.append(rdr.Value)
                elif in_t:
                    tbuf.append(rdr.Value)

            elif nt == XmlNodeType.EndElement:
                ln = rdr.LocalName
                if ln == u"v":
                    in_v = False
                    raw = u"".join(vbuf)
                    if ctype == u"s":
                        try:
                            k = int(raw)
                            raw = shared[k] if 0 <= k < len(shared) else None
                        except Exception:
                            raw = None
                    cells[col] = raw
                    if col > maxc:
                        maxc = col
                elif ln == u"t":
                    in_t = False
                    prev = cells.get(col) or u""
                    cells[col] = prev + u"".join(tbuf)
                    if col > maxc:
                        maxc = col
                elif ln == u"row":
                    rows.append([cells.get(i) for i in range(maxc + 1)])
    finally:
        rdr.Close()
    return rows


def _letters(ref):
    out = []
    for ch in ref:
        if ch.isalpha():
            out.append(ch)
        else:
            break
    return u"".join(out)


def _sheet_entry_name(zf):
    if zf.GetEntry(u"xl/worksheets/sheet1.xml") is not None:
        return u"xl/worksheets/sheet1.xml"
    for e in zf.Entries:
        fn = e.FullName
        if fn.startswith(u"xl/worksheets/") and fn.endswith(u".xml"):
            return fn
    return None


def read_xlsx_col_widths(path):
    u"""{индекс столбца (0-based): ширина Excel} из <cols>. {} — если нет."""
    fs = FileStream(path, FileMode.Open, FileAccess.Read)
    try:
        zf = ZipArchive(fs, ZipArchiveMode.Read)
        try:
            name = _sheet_entry_name(zf)
            xml = _read_entry(zf, name) if name else None
        finally:
            zf.Dispose()
    finally:
        fs.Close()

    out = {}
    if not xml:
        return out
    rdr = _reader(xml)
    try:
        while rdr.Read():
            if rdr.NodeType != XmlNodeType.Element:
                continue
            ln = rdr.LocalName
            if ln == u"col":
                try:
                    mn = int(rdr.GetAttribute(u"min"))
                    mx = int(rdr.GetAttribute(u"max"))
                    w = float(rdr.GetAttribute(u"width"))
                except (TypeError, ValueError):
                    continue
                for c in range(mn, mx + 1):
                    out[c - 1] = w
            elif ln == u"sheetData":
                break
    finally:
        rdr.Close()
    return out


def read_xlsx(path):
    u"""Вернуть список строк (список ячеек: unicode или None)."""
    fs = FileStream(path, FileMode.Open, FileAccess.Read)
    try:
        zf = ZipArchive(fs, ZipArchiveMode.Read)
        try:
            shared = _shared_strings(zf)
            sheet_name = _sheet_entry_name(zf)
            xml = _read_entry(zf, sheet_name) if sheet_name else None
        finally:
            zf.Dispose()
    finally:
        fs.Close()

    if not xml:
        return []

    try:
        return _parse_sheet(xml, shared)
    except Exception:
        return _parse_sheet_regex(xml, shared)


def _parse_sheet_regex(xml, shared):
    u"""Резервный разбор регулярками, если потоковый XmlReader почему-то упал."""
    rows = []
    for rm in re.finditer(r"<row\b[^>]*>(.*?)</row>", xml, re.S):
        body = rm.group(1)
        cells = {}
        maxc = -1
        for cm in re.finditer(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", body, re.S):
            attrs = cm.group(1) or u""
            inner = cm.group(2) or u""

            rmatch = re.search(r'r="([A-Z]+)\d+"', attrs)
            col = _col_index(rmatch.group(1)) if rmatch else (maxc + 1)

            tmatch = re.search(r't="([^"]+)"', attrs)
            typ = tmatch.group(1) if tmatch else None

            val = None
            if typ == u"inlineStr":
                chunks = re.findall(r"<t[^>]*>(.*?)</t>", inner, re.S)
                val = _unesc(u"".join(chunks))
            elif typ == u"s":
                vmatch = re.search(r"<v>(.*?)</v>", inner, re.S)
                if vmatch:
                    i = int(vmatch.group(1))
                    val = shared[i] if 0 <= i < len(shared) else None
            else:
                vmatch = re.search(r"<v>(.*?)</v>", inner, re.S)
                if vmatch:
                    val = _unesc(vmatch.group(1))

            cells[col] = val
            if col > maxc:
                maxc = col

        rows.append([cells.get(i) for i in range(maxc + 1)])

    return rows
