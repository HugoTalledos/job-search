"""Render resumes from Markdown to HTML and (when WeasyPrint is available) PDF."""

from __future__ import annotations

import logging
from pathlib import Path

import markdown

log = logging.getLogger(__name__)

CSS = """
body { font-family: 'Helvetica Neue', Arial, sans-serif; font-size: 10.5pt; line-height: 1.4;
       color: #222; max-width: 780px; margin: 0 auto; padding: 24px; }
h1 { font-size: 20pt; margin: 0 0 4px; }
h2 { font-size: 12pt; text-transform: uppercase; letter-spacing: .05em; border-bottom: 1px solid #999;
     margin: 18px 0 6px; padding-bottom: 2px; }
h3 { font-size: 11pt; margin: 10px 0 2px; }
ul { margin: 4px 0 4px 18px; padding: 0; }
li { margin: 2px 0; }
p { margin: 4px 0; }
a { color: #1a4f8b; text-decoration: none; }
@page { size: A4; margin: 14mm; }
"""


def to_html(md_text: str, title: str = "Resume") -> str:
    body = markdown.markdown(md_text, extensions=["extra", "sane_lists"])
    return f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title><style>{CSS}</style></head><body>{body}</body></html>"


def write_outputs(md_text: str, out_dir: Path, stem: str = "resume") -> dict[str, Path]:
    """Write .md and .html, plus .pdf when WeasyPrint is available. Returns written paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {"md": out_dir / f"{stem}.md", "html": out_dir / f"{stem}.html"}
    paths["md"].write_text(md_text)
    html = to_html(md_text)
    paths["html"].write_text(html)
    try:
        from weasyprint import HTML

        paths["pdf"] = out_dir / f"{stem}.pdf"
        HTML(string=html).write_pdf(paths["pdf"])
    except Exception as exc:  # optional dependency / missing system libs
        log.info("PDF not generated (%s); sending Markdown/HTML instead", type(exc).__name__)
    return paths
