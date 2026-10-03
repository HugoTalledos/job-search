"""Required PDF generation for CV requests, without access to external resources."""

from __future__ import annotations

from html import escape
from io import BytesIO

import markdown
from pypdf import PdfReader

from .markdown_renderer import CSS


class PdfRenderError(RuntimeError):
    """The CV could not be converted into a usable PDF."""


class _DenyResourceFetcher:
    # Recent WeasyPrint versions read this flag when a fetch fails; older ones accept any callable.
    _fail_on_errors = False

    def __call__(self, url: str):
        # No scheme is allowed: this also blocks file:, data: and internal network URLs.
        raise ValueError("Resource fetching is disabled for CV PDFs")


class RequiredPdfRenderer:
    def render(self, markdown_text: str) -> bytes:
        try:
            from weasyprint import HTML

            # Escape before Markdown processing so raw tags cannot add images, CSS or attachments.
            body = markdown.markdown(escape(markdown_text, quote=False), extensions=["extra", "sane_lists"])
            html = (
                "<!doctype html><html><head><meta charset='utf-8'><title>Resume</title>"
                f"<style>{CSS}</style></head><body>{body}</body></html>"
            )
            pdf = HTML(string=html, url_fetcher=_DenyResourceFetcher()).write_pdf()
            if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
                raise PdfRenderError("PDF renderer returned empty or invalid output")
            if not PdfReader(BytesIO(pdf)).pages:
                raise PdfRenderError("PDF renderer returned a document without pages")
            return pdf
        except PdfRenderError:
            raise
        except Exception as exc:
            raise PdfRenderError("Could not render the CV as a PDF") from exc
