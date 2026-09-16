"""Convert Markdown report body to a .docx Word document."""

from __future__ import annotations

import re
from io import BytesIO
from typing import Iterable

from docx import Document
from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_UL_RE = re.compile(r"^(\s*)([-*+])\s+(.*)$")
_OL_RE = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_HR_RE = re.compile(r"^(\*{3,}|-{3,}|_{3,})\s*$")
_TABLE_SEP_RE = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")
_INLINE_RE = re.compile(
    r"(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*]+\*|_[^_]+_|\[[^\]]+\]\([^)]+\))"
)


def _set_run_font(run, *, mono: bool = False) -> None:
    run.font.size = Pt(10 if mono else 11)
    if mono:
        run.font.name = "Courier New"
        r_pr = run._element.get_or_add_rPr()
        r_fonts = r_pr.get_or_add_rFonts()
        r_fonts.set(qn("w:ascii"), "Courier New")
        r_fonts.set(qn("w:hAnsi"), "Courier New")


def _add_inline_runs(paragraph, text: str) -> None:
    if not text:
        return
    pos = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > pos:
            run = paragraph.add_run(text[pos : match.start()])
            _set_run_font(run)
        token = match.group(0)
        if token.startswith("**") or token.startswith("__"):
            run = paragraph.add_run(token[2:-2])
            run.bold = True
            _set_run_font(run)
        elif token.startswith("`"):
            run = paragraph.add_run(token[1:-1])
            _set_run_font(run, mono=True)
        elif token.startswith("*") or token.startswith("_"):
            run = paragraph.add_run(token[1:-1])
            run.italic = True
            _set_run_font(run)
        elif token.startswith("["):
            label, _, rest = token[1:].partition("](")
            url = rest[:-1] if rest.endswith(")") else rest
            run = paragraph.add_run(label or url)
            run.font.color.rgb = RGBColor(0x0F, 0x43, 0x38)
            run.underline = True
            _set_run_font(run)
            if url:
                paragraph.add_run(f" ({url})")
        pos = match.end()
    if pos < len(text):
        run = paragraph.add_run(text[pos:])
        _set_run_font(run)


def _split_table_row(line: str) -> list[str]:
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [c.strip() for c in raw.split("|")]


def _add_table(doc: Document, header: list[str], rows: list[list[str]]) -> None:
    table = doc.add_table(rows=1 + len(rows), cols=len(header))
    table.style = "Table Grid"
    for i, cell_text in enumerate(header):
        cell = table.rows[0].cells[i]
        cell.text = ""
        p = cell.paragraphs[0]
        run = p.add_run(cell_text)
        run.bold = True
        _set_run_font(run)
    for r_idx, row in enumerate(rows):
        for c_idx in range(len(header)):
            cell = table.rows[r_idx + 1].cells[c_idx]
            cell.text = ""
            p = cell.paragraphs[0]
            value = row[c_idx] if c_idx < len(row) else ""
            _add_inline_runs(p, value)
    doc.add_paragraph("")


def _add_code_block(doc: Document, lines: Iterable[str]) -> None:
    for line in lines:
        p = doc.add_paragraph()
        run = p.add_run(line)
        _set_run_font(run, mono=True)
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.space_before = Pt(0)
    doc.add_paragraph("")


def markdown_to_docx_bytes(title: str, body_md: str) -> bytes:
    """Build a .docx from Markdown. Legacy .doc is not supported."""
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    heading = doc.add_heading(title or "Report", level=0)
    heading.alignment = WD_PARAGRAPH_ALIGNMENT.LEFT

    lines = (body_md or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    i = 0
    para_buf: list[str] = []

    def flush_para() -> None:
        nonlocal para_buf
        if not para_buf:
            return
        text = " ".join(para_buf).strip()
        para_buf = []
        if not text:
            return
        p = doc.add_paragraph()
        _add_inline_runs(p, text)

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            flush_para()
            i += 1
            code_lines: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            _add_code_block(doc, code_lines)
            if i < len(lines):
                i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and _TABLE_SEP_RE.match(
            lines[i + 1].strip()
        ):
            flush_para()
            header = _split_table_row(stripped)
            i += 2
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_split_table_row(lines[i]))
                i += 1
            if header:
                _add_table(doc, header, rows)
            continue

        if not stripped:
            flush_para()
            i += 1
            continue

        if _HR_RE.match(stripped):
            flush_para()
            p = doc.add_paragraph("―" * 24)
            p.paragraph_format.space_before = Pt(6)
            p.paragraph_format.space_after = Pt(6)
            i += 1
            continue

        heading_match = _HEADING_RE.match(stripped)
        if heading_match:
            flush_para()
            level = min(len(heading_match.group(1)), 4)
            h = doc.add_heading(heading_match.group(2).strip(), level=level)
            for run in h.runs:
                _set_run_font(run)
            i += 1
            continue

        ul = _UL_RE.match(line)
        if ul:
            flush_para()
            p = doc.add_paragraph(style="List Bullet")
            _add_inline_runs(p, ul.group(3))
            i += 1
            continue

        ol = _OL_RE.match(line)
        if ol:
            flush_para()
            p = doc.add_paragraph(style="List Number")
            _add_inline_runs(p, ol.group(3))
            i += 1
            continue

        para_buf.append(stripped)
        i += 1

    flush_para()

    if len(doc.paragraphs) <= 1 and not (body_md or "").strip():
        doc.add_paragraph("(empty report)")

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
