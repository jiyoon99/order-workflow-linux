from __future__ import annotations

import html
import io
import os
import re
import subprocess
import tempfile
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree as ET

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._finish_row()
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self._finish_cell()
            self.cell = []
        elif tag == "br" and self.cell is not None:
            self.cell.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"}:
            self._finish_cell()
        elif tag == "tr":
            self._finish_row()

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)

    def close(self) -> None:
        super().close()
        self._finish_row()

    def _finish_cell(self) -> None:
        if self.cell is not None and self.row is not None:
            self.row.append("".join(self.cell).strip())
        self.cell = None

    def _finish_row(self) -> None:
        self._finish_cell()
        if self.row and any(value for value in self.row):
            self.rows.append(self.row)
        self.row = None


def _read_html_table(content: bytes) -> list[dict[str, str]]:
    parser = _TableParser()
    parser.feed(content.decode("utf-8-sig", errors="replace"))
    parser.close()
    if len(parser.rows) < 2:
        return []
    headers = [value.strip().replace("\n", "") for value in parser.rows[0]]
    return [
        {header: row[index] if index < len(row) else "" for index, header in enumerate(headers) if header}
        for row in parser.rows[1:]
        if any(value.strip() for value in row)
    ]


def _column_number(reference: str) -> int:
    result = 0
    for char in re.match(r"[A-Z]+", reference.upper()).group(0):
        result = result * 26 + ord(char) - 64
    return result


def _convert_xls_to_xlsx(content: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="order-workflow-xls-") as directory:
        temporary = Path(directory)
        source = temporary / "upload.xls"
        output = temporary / "output"
        profile = temporary / "profile"
        source.write_bytes(content)
        output.mkdir()
        profile.mkdir()
        command = [
            "/usr/bin/libreoffice", "--headless", "--nologo", "--nodefault", "--nofirststartwizard",
            f"-env:UserInstallation={profile.as_uri()}", "--convert-to", "xlsx", "--outdir", str(output), str(source),
        ]
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                env={**os.environ, "HOME": str(temporary)},
                timeout=30,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as error:
            raise ValueError(".xls 파일을 변환하지 못했습니다.") from error
        converted = output / "upload.xlsx"
        if result.returncode != 0 or not converted.exists():
            raise ValueError("올바른 .xls 파일인지 확인하세요.")
        return converted.read_bytes()


def read_first_sheet(content: bytes, source_file: str = "") -> list[dict[str, str]]:
    if content.lstrip().lower().startswith((b"<html", b"<!doctype html")):
        return _read_html_table(content)
    if source_file.lower().endswith(".xls") or content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        content = _convert_xls_to_xlsx(content)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{{{MAIN_NS}}}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t")))

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {item.attrib["Id"]: item.attrib["Target"] for item in relationships}
        sheet = workbook.find(f"{{{MAIN_NS}}}sheets/{{{MAIN_NS}}}sheet")
        if sheet is None:
            return []
        target = targets[sheet.attrib[f"{{{REL_NS}}}id"]].lstrip("/")
        if not target.startswith("xl/"):
            target = f"xl/{target}"

        root = ET.fromstring(archive.read(target))
        rows: list[list[str]] = []
        for row_node in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row"):
            values: dict[int, str] = {}
            for cell in row_node.findall(f"{{{MAIN_NS}}}c"):
                reference = cell.attrib.get("r", "A1")
                cell_type = cell.attrib.get("t", "")
                value_node = cell.find(f"{{{MAIN_NS}}}v")
                value = value_node.text if value_node is not None and value_node.text else ""
                if cell_type == "s" and value:
                    value = shared[int(value)]
                elif cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))
                values[_column_number(reference)] = value
            if values:
                rows.append([values.get(index, "") for index in range(1, max(values) + 1)])

    if len(rows) < 2:
        return []
    markers = {
        "주문번호", "결제번호", "상품명", "등록상품명", "노출상품명(옵션명)",
        "수령인명", "수취인이름", "수령인", "받는분", "구매자명",
    }
    header_index = 0
    best_score = -1
    for index, row in enumerate(rows[:20]):
        normalized = [str(value).strip().replace("\n", "") for value in row]
        score = sum(value in markers for value in normalized) * 100 + sum(bool(value) for value in normalized)
        if score > best_score:
            header_index, best_score = index, score
    headers = [str(value).strip().replace("\n", "") for value in rows[header_index]]
    if not any(headers):
        return []
    return [
        {header: row[index] if index < len(row) else "" for index, header in enumerate(headers) if header}
        for row in rows[header_index + 1:]
        if any(str(value).strip() for value in row)
    ]


def _column_name(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(65 + remainder) + result
    return result


def write_xlsx(headers: list[str], rows: list[list[object]]) -> bytes:
    all_rows = [headers, *rows]
    row_xml = []
    for row_index, row in enumerate(all_rows, 1):
        cells = []
        for column_index, value in enumerate(row, 1):
            reference = f"{_column_name(column_index)}{row_index}"
            escaped = html.escape(str(value if value is not None else ""))
            cells.append(f'<c r="{reference}" t="inlineStr"><is><t>{escaped}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')

    worksheet = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="{MAIN_NS}"><sheetData>{"".join(row_xml)}</sheetData></worksheet>'''
    workbook = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="{MAIN_NS}" xmlns:r="{REL_NS}"><sheets><sheet name="출고완료" sheetId="1" r:id="rId1"/></sheets></workbook>'''
    content_types = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>'''
    root_rels = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{PACKAGE_REL_NS}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>'''
    workbook_rels = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="{PACKAGE_REL_NS}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>'''

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", root_rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)
    return output.getvalue()
