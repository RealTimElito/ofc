"""Build a self-contained HTML export, optionally matching an imported Word theme."""

from __future__ import annotations

import base64
import html
import mimetypes
from pathlib import Path
from typing import Any, Optional

import markdown as md

from app.services.export_docx import strip_redundant_title_heading
from app.services.theme import empty_theme, theme_has_visuals


def _css_font_stack(name: Optional[str], fallback: str) -> str:
    if not name:
        return fallback
    safe = name.replace("\\", "").replace('"', "")
    return f'"{safe}", {fallback}'


def _data_uri(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def _chrome_block(
    text: str,
    logo_uri: Optional[str],
    *,
    css_class: str,
) -> str:
    parts: list[str] = []
    if logo_uri:
        parts.append(
            f'<img class="theme-logo" src="{logo_uri}" alt="" />'
        )
    if text.strip():
        parts.append(f"<span>{html.escape(text.strip())}</span>")
    if not parts:
        return ""
    return f'<div class="{css_class}">{" ".join(parts)}</div>'


def render_html_export(
    title: str,
    body_md: str,
    *,
    theme: Optional[dict[str, Any]] = None,
    theme_assets_dir: Optional[Path] = None,
) -> str:
    """Return a standalone HTML document for download / offline viewing."""
    theme = {**empty_theme(), **(theme or {})}
    body_md = strip_redundant_title_heading(title, body_md or "")
    body = md.markdown(body_md, extensions=["tables", "fenced_code"])
    safe_title = html.escape(title or "Report")

    body_font = _css_font_stack(theme.get("body_font"), "Georgia, serif")
    heading_font = _css_font_stack(
        theme.get("heading_font") or theme.get("body_font"),
        "system-ui, sans-serif",
    )
    body_size = theme.get("body_size_pt") or 11
    heading_size = theme.get("heading_size_pt") or 14
    title_size = theme.get("title_size_pt") or 26

    pad_top = theme.get("margin_top_in")
    pad_bottom = theme.get("margin_bottom_in")
    pad_left = theme.get("margin_left_in")
    pad_right = theme.get("margin_right_in")
    # Convert inches → rem-ish padding when theme set margins; else defaults.
    def _pad(inches: Any, default_rem: float) -> str:
        if inches is None:
            return f"{default_rem}rem"
        try:
            return f"{max(0.5, float(inches) * 1.1):.2f}rem"
        except (TypeError, ValueError):
            return f"{default_rem}rem"

    padding = (
        f"{_pad(pad_top, 2)} {_pad(pad_right, 1)} "
        f"{_pad(pad_bottom, 2)} {_pad(pad_left, 1)}"
    )

    assets = theme_assets_dir or Path(".")
    header_logo = None
    footer_logo = None
    if theme.get("header_logo"):
        header_logo = _data_uri(assets / str(theme["header_logo"]))
    if theme.get("footer_logo"):
        footer_logo = _data_uri(assets / str(theme["footer_logo"]))

    header_html = _chrome_block(
        theme.get("header_text") or "",
        header_logo,
        css_class="theme-header",
    )
    footer_html = _chrome_block(
        theme.get("footer_text") or "",
        footer_logo,
        css_class="theme-footer",
    )
    source = (theme.get("source_label") or "").strip()
    source_html = (
        f'<p class="theme-source">Theme: {html.escape(source)}</p>'
        if source and theme_has_visuals(theme)
        else ""
    )

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>{safe_title}</title>
<style>
body{{font-family:{body_font};font-size:{float(body_size)}pt;max-width:720px;margin:0 auto;padding:{padding};line-height:1.55;color:#1a1a1a}}
h1,h2,h3{{font-family:{heading_font}}}
h1{{font-size:{float(title_size)}pt}}
h2,h3{{font-size:{float(heading_size)}pt}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #ccc;padding:.4rem .6rem}}
code{{background:#f4f4f4;padding:.1rem .3rem}} pre{{background:#f4f4f4;padding:1rem;overflow:auto}}
.theme-header,.theme-footer{{display:flex;align-items:center;gap:.75rem;font-size:.85rem;color:#444;border-color:#ddd}}
.theme-header{{border-bottom:1px solid #ddd;padding-bottom:.6rem;margin-bottom:1.25rem}}
.theme-footer{{border-top:1px solid #ddd;padding-top:.6rem;margin-top:1.75rem}}
.theme-logo{{height:28px;width:auto;object-fit:contain}}
.theme-source{{font-size:.75rem;color:#777;margin-top:.5rem}}
</style></head><body>
{header_html}
<h1>{safe_title}</h1>
{body}
{footer_html}
{source_html}
</body></html>"""
