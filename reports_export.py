"""File writers for the report centre.

The deployment installs nothing beyond Flask, so the two office formats the
report centre offers are written here against their published containers: an
.xlsx workbook is a zip of OOXML parts and a .pdf is a small tagged file with
built-in Helvetica fonts. Both take the rows the screen already rendered, so an
export can never show something the server would refuse to display.

The public functions are xlsx_bytes, pdf_bytes and letter_pdf_bytes. Each
returns the file body as bytes and raises ValueError only for a row that is
wider than the header.
"""
import zipfile
import io
import math
import zlib
from xml.sax.saxutils import escape

MONEY = "money"
NUMBER = "number"


def _clean(value):
    """A value as printable text: no control characters XML/PDF reject."""
    if value is None:
        return ""
    text = str(value)
    return "".join(ch for ch in text if ch >= " " or ch in "\t\n\r")


def _text(value):
    return _clean(value).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")


def _xml(value):
    return escape(_clean(value))


# ---------------------------------------------------------------- xlsx ----
def _column_name(index):
    name = ""
    index += 1
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _cell(reference, value, style, numeric):
    if value is None or value == "":
        return ""
    if numeric and isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return ""
        style_attr = ' s="{}"'.format(style) if style else ""
        return '<c r="{}"{}><v>{}</v></c>'.format(reference, style_attr, value)
    shown = _text(value)[:32767]
    style_attr = ' s="{}"'.format(style) if style else ""
    return ('<c r="{}"{} t="inlineStr"><is><t xml:space="preserve">{}</t></is></c>'
            .format(reference, style_attr, _xml(shown)))


def _column_widths(columns, rows):
    """Readable widths from the header and a sample of the values."""
    widths = []
    for position, column in enumerate(columns):
        label = column[1]
        widest = len(str(label))
        for row in rows[:200]:
            value = row[position]
            if isinstance(value, float):
                shown = "{:,.2f}".format(value)
            else:
                shown = _text(value)
            widest = max(widest, len(shown))
        widths.append(max(10, min(widest + 2, 46)))
    return widths


def _sheet_xml(columns, rows, title, meta, money_columns):
    header_row = 2 + len(meta)
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
             '<sheetViews><sheetView tabSelected="1" workbookViewId="0">',
             '<pane ySplit="{}" topLeftCell="A{}" activePane="bottomLeft"'
             ' state="frozen"/></sheetView></sheetViews>'.format(
                 header_row, header_row + 1),
             '<sheetFormatPr defaultRowHeight="15"/>',
             '<cols>']
    for position, width in enumerate(_column_widths(columns, rows)):
        parts.append('<col min="{0}" max="{0}" width="{1}" customWidth="1"/>'.format(
            position + 1, width))
    parts.append('</cols><sheetData>')

    parts.append('<row r="1" ht="20" customHeight="1"><c r="A1" s="4" t="inlineStr">'
                 '<is><t xml:space="preserve">{}</t></is></c></row>'.format(_xml(title)))
    for offset, line in enumerate(meta):
        parts.append('<row r="{}"><c r="A{}" s="3" t="inlineStr">'
                     '<is><t xml:space="preserve">{}</t></is></c></row>'.format(
                         offset + 2, offset + 2, _xml(line)))

    parts.append('<row r="{}" ht="18" customHeight="1">'.format(header_row))
    for position, (_key, label) in enumerate(columns):
        parts.append(_cell("{}{}".format(_column_name(position), header_row),
                           label, 1, False))
    parts.append('</row>')

    for offset, row in enumerate(rows):
        line = header_row + 1 + offset
        parts.append('<row r="{}">'.format(line))
        for position, value in enumerate(row):
            numeric = columns[position][0] in money_columns
            style = 2 if numeric else 0
            parts.append(_cell("{}{}".format(_column_name(position), line),
                               value, style, numeric))
        parts.append('</row>')

    parts.append('</sheetData>')
    if rows:
        parts.append('<autoFilter ref="A{0}:{1}{2}"/>'.format(
            header_row, _column_name(len(columns) - 1), header_row + len(rows)))
    parts.append('<pageMargins left="0.4" right="0.4" top="0.5" bottom="0.5"'
                 ' header="0.3" footer="0.3"/>')
    parts.append('</worksheet>')
    return "".join(parts)


def _styles_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts count="2">'
        '<numFmt numFmtId="164" formatCode="#,##0.00"/>'
        '<numFmt numFmtId="165" formatCode="#,##0.##"/>'
        '</numFmts>'
        '<fonts count="3">'
        '<font><sz val="10"/><name val="Calibri"/><family val="2"/></font>'
        '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '<font><b/><sz val="14"/><color rgb="FF3A2A1D"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="3">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF4A3426"/>'
        '<bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="2">'
        '<border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border>'
        '<left style="thin"><color rgb="FFD9D2C7"/></left>'
        '<right style="thin"><color rgb="FFD9D2C7"/></right>'
        '<top style="thin"><color rgb="FFD9D2C7"/></top>'
        '<bottom style="thin"><color rgb="FFD9D2C7"/></bottom>'
        '<diagonal/></border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
        '</cellStyleXfs>'
        '<cellXfs count="5">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0"'
        ' applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1">'
        '<alignment horizontal="left" vertical="center" wrapText="1"/></xf>'
        '<xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0"'
        ' applyNumberFormat="1" applyBorder="1" applyAlignment="1">'
        '<alignment horizontal="right" vertical="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"'
        ' applyAlignment="1"><alignment horizontal="left"/></xf>'
        '<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="0"'
        ' applyFont="1"/>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
        '</cellStyles>'
        '</styleSheet>'
    )


def xlsx_bytes(title, meta, columns, rows, money_columns=()):
    """One styled worksheet: title, filter summary, frozen header, data."""
    money = frozenset(money_columns)
    sheet = _sheet_xml(columns, rows, title, meta, money)
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Report" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels"'
        ' ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml"'
        ' ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        '</Types>'
    )
    package_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"'
        ' Target="xl/workbook.xml"/></Relationships>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"'
        ' Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2"'
        ' Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles"'
        ' Target="styles.xml"/></Relationships>'
    )

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", package_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/styles.xml", _styles_xml())
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
    return buffer.getvalue()


# ----------------------------------------------------------------- pdf ----
PAGE_WIDTH = 842.0          # A4 landscape, points
PAGE_HEIGHT = 595.0
MARGIN = 32.0
FONT = 8.4
LINE = 15.0
HEADER_LINE = 17.0


def _pdf_escape(text):
    raw = _text(text).encode("cp1252", "replace").decode("latin-1")
    return raw.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _width(text, size=FONT):
    """Helvetica is close enough to half the point size per character for clipping."""
    return len(text) * size * 0.5


def _clip(text, room, size=FONT):
    if room <= 0:
        return ""
    limit = max(1, int(room / (size * 0.5)))
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)] + "…"


def _columns_widths(columns, rows):
    weights = []
    for position, column in enumerate(columns):
        label = str(column[1])
        widest = len(label)
        for row in rows[:80]:
            value = row[position]
            shown = "{:,.2f}".format(value) if isinstance(value, float) else _text(value)
            widest = max(widest, min(len(shown), 40))
        weights.append(max(6, min(widest, 28)))
    total = float(sum(weights)) or 1.0
    room = PAGE_WIDTH - 2 * MARGIN
    widths = [room * weight / total for weight in weights]
    # nothing narrower than a header can be read in
    widths = [max(34.0, width) for width in widths]
    scale = room / sum(widths)
    return [width * scale for width in widths]


def _text_op(x, y, text, size, bold=False):
    return "BT /{} {:.2f} Tf 1 0 0 1 {:.2f} {:.2f} Tm ({}) Tj ET".format(
        "F2" if bold else "F1", size, x, y, _pdf_escape(text))


def _money(value):
    return "{:,.2f}".format(float(value))


def _pdf_pages(title, meta, columns, rows, money_columns):
    """Lay the table out, returning one content stream per page."""
    widths = _columns_widths(columns, rows)
    money = frozenset(money_columns)
    header_lines = [("h1", title)] + [("meta", line) for line in meta]
    pages = []
    position = 0
    while True:
        content = []
        y = PAGE_HEIGHT - MARGIN
        if not pages:
            y -= 6
            for _kind, line in header_lines:
                if _kind == "h1":
                    content.append(_text_op(MARGIN, y, _clip(line, PAGE_WIDTH - 2 * MARGIN, 16), 16, True))
                    y -= 22
                else:
                    content.append(_text_op(MARGIN, y, _clip(line, PAGE_WIDTH - 2 * MARGIN, 8), 8))
                    y -= 12
            y -= 6
        else:
            content.append(_text_op(MARGIN, y, _clip("{} (continued)".format(title),
                                                     PAGE_WIDTH - 2 * MARGIN, 11), 11, True))
            y -= 24

        header_top = y
        content.append("0.29 0.20 0.15 rg {:.2f} {:.2f} {:.2f} {:.2f} re f".format(
            MARGIN, header_top - HEADER_LINE + 4, PAGE_WIDTH - 2 * MARGIN, HEADER_LINE))
        x = MARGIN
        for position_, (key, label) in enumerate(columns):
            content.append("1 1 1 rg")
            content.append(_text_op(x + 3, header_top - 9,
                                    _clip(str(label), widths[position_] - 6), FONT, True))
            x += widths[position_]
        y = header_top - HEADER_LINE
        content.append("0.29 0.20 0.15 RG 0.6 w {:.2f} {:.2f} m {:.2f} {:.2f} l S".format(
            MARGIN, header_top - HEADER_LINE + 4, MARGIN + PAGE_WIDTH - 2 * MARGIN,
            header_top - HEADER_LINE + 4))

        while position < len(rows):
            if y - LINE < MARGIN + 26:
                break
            row = rows[position]
            if position % 2 == 1:
                content.append("0.965 0.960 0.950 rg {:.2f} {:.2f} {:.2f} {:.2f} re f".format(
                    MARGIN, y - LINE + 4, PAGE_WIDTH - 2 * MARGIN, LINE))
            x = MARGIN
            for offset, (key, _label) in enumerate(columns):
                value = row[offset]
                text = _money(value) if key in money and isinstance(value, (int, float)) else _text(value)
                room = widths[offset] - 6
                text = _clip(text, room)
                if key in money and isinstance(value, (int, float)):
                    x_text = x + widths[offset] - 3 - _width(text)
                else:
                    x_text = x + 3
                content.append("0.13 0.12 0.11 rg")
                content.append(_text_op(x_text, y, text, FONT))
                x += widths[offset]
            content.append("0.86 0.84 0.80 RG 0.4 w {:.2f} {:.2f} m {:.2f} {:.2f} l S".format(
                MARGIN, y - LINE + 4, MARGIN + PAGE_WIDTH - 2 * MARGIN, y - LINE + 4))
            y -= LINE
            position += 1

        if not pages and not rows:
            content.append(_text_op(MARGIN + 3, header_top - HEADER_LINE - 16,
                                    "No rows match the selected filters.", FONT))
        pages.append((content, position))
        if position >= len(rows):
            break
    return [content for content, _position in pages], widths


def pdf_bytes(title, meta, columns, rows, money_columns=()):
    """A landscape table with a repeated header, page numbers and a filter note."""
    pages, _widths = _pdf_pages(title, meta, columns, rows, money_columns)
    objects = _document_objects(pages, title)
    return _assemble_pdf(objects)


def _document_objects(pages, footer_title, width=PAGE_WIDTH, height=PAGE_HEIGHT,
                      margin=MARGIN):
    """Assemble the PDF objects for one content stream per page."""
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)  # 1-based object number

    catalog = add(b"")  # reserved: 1
    leaf_pages = add(b"")  # reserved: 2
    font_regular = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica"
                       b" /Encoding /WinAnsiEncoding >>")
    font_bold = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold"
                    b" /Encoding /WinAnsiEncoding >>")

    page_ids = []
    for number, content_lines in enumerate(pages, start=1):
        footer = _clip("{} - page {} of {}".format(footer_title, number, len(pages)),
                       width - 2 * margin, 7.5)
        content_lines.append(_text_op(margin, margin - 8, footer, 7.5))
        stream = zlib.compress("\n".join(content_lines).encode("cp1252", "replace"), 6)
        content_id = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(stream)
                         + stream + b"\nendstream")
        page_id = add((
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {:.0f} {:.0f}]"
            " /Resources << /Font << /F1 {} 0 R /F2 {} 0 R >> >> /Contents {} 0 R >>"
        ).format(width, height, font_regular, font_bold,
                 content_id).encode("ascii"))
        page_ids.append(page_id)

    objects[catalog - 1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    kids = " ".join("{} 0 R".format(page_id) for page_id in page_ids)
    objects[leaf_pages - 1] = (
        "<< /Type /Pages /Kids [{}] /Count {} >>".format(kids, len(page_ids))
    ).encode("ascii")
    return objects


def _assemble_pdf(objects):
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(output))
        output += "{} 0 obj\n".format(number).encode("ascii") + body + b"\nendobj\n"
    xref = len(output)
    output += "xref\n0 {}\n".format(len(objects) + 1).encode("ascii")
    output += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        output += "{:010d} 00000 n \n".format(offset).encode("ascii")
    output += (
        "trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{}\n%%EOF\n".format(
            len(objects) + 1, xref)
    ).encode("ascii")
    return bytes(output)


# ---------------------------------------------------------- letter sheet ----
LETTER_PAGE_WIDTH = 595.0
LETTER_PAGE_HEIGHT = 842.0
LETTER_MARGIN = 56.0


def _wrap_text(text, room, size):
    """Break prose into lines that fit the room, keeping paragraph breaks."""
    limit = max(8, int(room / (size * 0.5)))
    lines = []
    for paragraph in _clean(text).split("\n"):
        words = paragraph.split()
        if not words:
            lines.append("")
            continue
        line = words[0]
        for word in words[1:]:
            if len(line) + 1 + len(word) <= limit:
                line = "{} {}".format(line, word)
            else:
                lines.append(line)
                line = word
        lines.append(line)
    return lines


def letter_pdf_bytes(title, subtitle, meta, body, signature=()):
    """A portrait letter on the company sheet: heading, meta, prose, signature.

    The letter register offers this as the document download. It uses the same
    Helvetica fonts and container as the report exports, so nothing beyond Flask
    is needed to write it.
    """
    width, height, margin = LETTER_PAGE_WIDTH, LETTER_PAGE_HEIGHT, LETTER_MARGIN
    room = width - 2 * margin
    footer_title = "{} - {}".format(title, meta[0][1] if meta else "")

    def continuation():
        return [_text_op(margin, height - margin,
                         _clip("{} (continued)".format(title), room, 11), 11, True),
                _text_op(margin, height - margin - 18,
                         _clip(meta[0][1] if meta else "", room, 8.5), 8.5),
                "0.29 0.20 0.15 RG 0.6 w {:.2f} {:.2f} m {:.2f} {:.2f} l S".format(
                    margin, height - margin - 26, margin + room,
                    height - margin - 26)]

    content = [_text_op(margin, height - margin, _clip(title, room, 16), 16, True)]
    y = height - margin - 20
    if subtitle:
        content.append(_text_op(margin, y, _clip(subtitle, room, 9.5), 9.5))
        y -= 14
    content.append("0.29 0.20 0.15 RG 0.8 w {:.2f} {:.2f} m {:.2f} {:.2f} l S".format(
        margin, y, margin + room, y))
    y -= 22

    for label, value in meta:
        content.append("0.13 0.12 0.11 rg")
        content.append(_text_op(margin, y, _clip("{}:".format(label), room, 8.5),
                                8.5, True))
        content.append(_text_op(margin + 70, y,
                                _clip(_text(value), room - 70, 9.5), 9.5))
        y -= 14

    pages = []
    y -= 10
    content.append("0.29 0.20 0.15 RG 0.8 w {:.2f} {:.2f} m {:.2f} {:.2f} l S".format(
        margin, y, margin + room, y))
    y -= 22

    for line in _wrap_text(body, room, 10):
        if y < margin + 34:
            pages.append(content)
            content = continuation()
            y = height - margin - 44
        content.append("0.13 0.12 0.11 rg")
        content.append(_text_op(margin, y, line, 10))
        y -= 14

    if signature:
        if y < margin + 34 + 16 * len(signature):
            pages.append(content)
            content = continuation()
            y = height - margin - 44
        y -= 18
        content.append("0.29 0.20 0.15 RG 0.6 w {:.2f} {:.2f} m {:.2f} {:.2f} l S".format(
            margin, y, margin + 200, y))
        y -= 16
        for line in signature:
            content.append("0.13 0.12 0.11 rg")
            content.append(_text_op(margin, y, _clip(line, room, 9.5), 9.5))
            y -= 14
    pages.append(content)
    objects = _document_objects(pages, footer_title, width, height, margin)
    return _assemble_pdf(objects)
