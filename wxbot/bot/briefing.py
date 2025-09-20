"""Briefing document generation utilities."""

from __future__ import annotations

import html
import io
import zipfile
from datetime import datetime, timezone
from typing import Mapping, Sequence

from .formatter import StationConditions, evaluate_station


def build_briefing_text(
    icaos: Sequence[str],
    wx_bundle: Mapping[str, Mapping[str, Sequence[str]]],
) -> str:
    """Compose a textual briefing in НАМС-86 style."""

    now = datetime.now(timezone.utc)
    lines: list[str] = []
    lines.append("НАМС-86 — Метеорологическая справка")
    lines.append(f"Дата/время (UTC): {now:%Y-%m-%d %H:%MZ}")
    lines.append(f"Станции: {', '.join(icaos)}")
    lines.append("")

    for icao in icaos:
        station_data = wx_bundle.get(icao, {})
        metars = station_data.get("metar", [])
        specis = station_data.get("speci", [])
        tafs = station_data.get("taf", [])
        conditions = evaluate_station(metars, now)
        category = conditions.category or "нет данных"
        age_fragment = _format_age(conditions)

        lines.append(f"{icao} — {category} ({age_fragment})")
        lines.extend(_section_lines("METAR", metars))
        lines.extend(_section_lines("SPECI", specis))
        lines.extend(_section_lines("TAF", tafs, empty_hint="TAF: нет данных"))
        lines.append("")

    lines.append("Источник: NOAA ADDS, OurAirports (локальный справочник)")
    return "\n".join(lines)


def _format_age(conditions: StationConditions) -> str:
    if conditions.age_hours is None:
        return "age~?h"
    return f"age~{conditions.age_hours}h"


def _section_lines(label: str, reports: Sequence[str], *, empty_hint: str | None = None) -> list[str]:
    if reports:
        section = [f"{label}:"]
        section.extend(reports)
        return section
    hint = empty_hint or f"{label}: нет данных"
    return [hint]


def render_docx(text: str) -> bytes:
    """Render the textual briefing into a DOCX document."""

    buffer = io.BytesIO()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", _CONTENT_TYPES_XML)
        docx.writestr("_rels/.rels", _RELS_XML)
        docx.writestr("word/_rels/document.xml.rels", _DOC_RELS_XML)
        docx.writestr("word/document.xml", _build_document_xml(text))
        docx.writestr("docProps/app.xml", _APP_XML)
        docx.writestr("docProps/core.xml", _CORE_XML.format(timestamp=timestamp))
    return buffer.getvalue()


def _build_document_xml(text: str) -> str:
    paragraphs: list[str] = []
    for line in text.splitlines():
        if line.strip():
            paragraphs.append(
                "<w:p><w:r><w:t>{}</w:t></w:r></w:p>".format(_xml_escape(line))
            )
        else:
            paragraphs.append("<w:p/>")

    paragraphs.append(
        "<w:sectPr><w:pgSz w:w=\"11906\" w:h=\"16838\"/>"
        "<w:pgMar w:top=\"1440\" w:right=\"1440\" w:bottom=\"1440\" w:left=\"1440\"/>"
        "</w:sectPr>"
    )

    body = "".join(paragraphs)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>"
        "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
        f"<w:body>{body}</w:body>"
        "</w:document>"
    )


def render_pdf(text: str) -> bytes:
    """Render the textual briefing into a simple PDF document."""

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    positions: dict[int, int] = {}

    def write_object(obj_id: int, content: bytes) -> None:
        positions[obj_id] = buffer.tell()
        buffer.write(f"{obj_id} 0 obj\n".encode("ascii"))
        buffer.write(content)
        buffer.write(b"\nendobj\n")

    # Catalog, pages, page
    write_object(1, b"<< /Type /Catalog /Pages 2 0 R >>")
    write_object(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
    write_object(
        3,
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
    )

    content_stream = _build_pdf_stream(text)
    stream_header = f"<< /Length {len(content_stream)} >>\n".encode("ascii")
    positions[4] = buffer.tell()
    buffer.write(b"4 0 obj\n")
    buffer.write(stream_header)
    buffer.write(b"stream\n")
    buffer.write(content_stream)
    buffer.write(b"\nendstream\nendobj\n")

    write_object(5, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    startxref = buffer.tell()
    buffer.write(b"xref\n0 6\n0000000000 65535 f \n")
    for obj_id in range(1, 6):
        offset = positions.get(obj_id, 0)
        buffer.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    buffer.write(b"trailer << /Size 6 /Root 1 0 R >>\n")
    buffer.write(b"startxref\n")
    buffer.write(f"{startxref}\n".encode("ascii"))
    buffer.write(b"%%EOF")
    return buffer.getvalue()


def _build_pdf_stream(text: str) -> bytes:
    lines = text.splitlines() or [""]
    parts = ["BT", "/F1 12 Tf", "14 TL", "50 800 Td"]
    for index, line in enumerate(lines):
        escaped = _pdf_escape(line)
        if index == 0:
            parts.append(f"({escaped}) Tj")
        else:
            parts.append("T*")
            parts.append(f"({escaped}) Tj")
    parts.append("ET")
    joined = "\n".join(parts) + "\n"
    return joined.encode("latin-1", errors="replace")


def _xml_escape(value: str) -> str:
    return html.escape(value, quote=False)


def _pdf_escape(value: str) -> str:
    transliterated = _transliterate_for_pdf(value)
    escaped = transliterated.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    return escaped


def _transliterate_for_pdf(value: str) -> str:
    mapping = {
        "А": "A",
        "Б": "B",
        "В": "V",
        "Г": "G",
        "Д": "D",
        "Е": "E",
        "Ё": "E",
        "Ж": "Zh",
        "З": "Z",
        "И": "I",
        "Й": "Y",
        "К": "K",
        "Л": "L",
        "М": "M",
        "Н": "N",
        "О": "O",
        "П": "P",
        "Р": "R",
        "С": "S",
        "Т": "T",
        "У": "U",
        "Ф": "F",
        "Х": "Kh",
        "Ц": "Ts",
        "Ч": "Ch",
        "Ш": "Sh",
        "Щ": "Shch",
        "Ъ": "",
        "Ы": "Y",
        "Ь": "",
        "Э": "E",
        "Ю": "Yu",
        "Я": "Ya",
    }
    result_chars: list[str] = []
    for char in value:
        if "A" <= char <= "Z" or "a" <= char <= "z" or "0" <= char <= "9" or char in " ,.:;-_" or ord(char) < 128:
            result_chars.append(char)
        else:
            upper = mapping.get(char.upper())
            if upper is None:
                result_chars.append("?")
            else:
                result_chars.append(upper if char.isupper() else upper.lower())
    return "".join(result_chars)


_CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""

_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

_DOC_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
"""

_APP_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">
  <Application>WX Bot</Application>
  <DocSecurity>0</DocSecurity>
  <ScaleCrop>false</ScaleCrop>
  <HeadingPairs>
    <vt:vector size="2" baseType="variant">
      <vt:variant>
        <vt:lpstr>Paragraphs</vt:lpstr>
      </vt:variant>
      <vt:variant>
        <vt:i4>1</vt:i4>
      </vt:variant>
    </vt:vector>
  </HeadingPairs>
  <TitlesOfParts>
    <vt:vector size="1" baseType="lpstr">
      <vt:lpstr>Briefing</vt:lpstr>
    </vt:vector>
  </TitlesOfParts>
  <Company>WX Bot</Company>
  <LinksUpToDate>false</LinksUpToDate>
  <SharedDoc>false</SharedDoc>
  <HyperlinksChanged>false</HyperlinksChanged>
  <AppVersion>16.0000</AppVersion>
</Properties>
"""

_CORE_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <dc:creator>WX Bot</dc:creator>
  <cp:lastModifiedBy>WX Bot</cp:lastModifiedBy>
  <dcterms:created xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:created>
  <dcterms:modified xsi:type="dcterms:W3CDTF">{timestamp}</dcterms:modified>
</cp:coreProperties>
"""


__all__ = ["build_briefing_text", "render_docx", "render_pdf"]
