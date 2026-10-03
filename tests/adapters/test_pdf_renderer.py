from io import BytesIO

import pytest
from pypdf import PdfReader

from job_agent.adapters.resume.pdf_renderer import PdfRenderError, RequiredPdfRenderer


def test_pdf_render_produces_readable_pdf_bytes():
    result = RequiredPdfRenderer().render("# Candidate\n\n## Experience\n- Python developer")

    assert isinstance(result, bytes)
    assert result.startswith(b"%PDF")
    reader = PdfReader(BytesIO(result))
    assert len(reader.pages) == 1
    assert "Candidate" in reader.pages[0].extract_text()
    assert "Python developer" in reader.pages[0].extract_text()


@pytest.mark.parametrize("output", [b"", None, "not bytes", b"not a PDF", b"%PDF-1.7\n%%EOF\n"])
def test_pdf_render_rejects_invalid_output(monkeypatch, output):
    monkeypatch.setattr("weasyprint.HTML.write_pdf", lambda self: output)

    with pytest.raises(PdfRenderError):
        RequiredPdfRenderer().render("# Candidate")


def test_pdf_render_wraps_weasyprint_failure(monkeypatch):
    def fail(self):
        raise RuntimeError("native renderer unavailable")

    monkeypatch.setattr("weasyprint.HTML.write_pdf", fail)

    with pytest.raises(PdfRenderError) as error:
        RequiredPdfRenderer().render("# Candidate")

    assert isinstance(error.value.__cause__, RuntimeError)


def test_raw_html_is_rendered_as_text(monkeypatch):
    calls = []

    def forbidden_fetch(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("External resource accessed")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbidden_fetch)
    result = RequiredPdfRenderer().render(
        '# Candidate\n\n<img src="https://example.invalid/cv.png">\n\n'
        '<style>@import url("https://example.invalid/style.css");</style>'
    )

    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(result)).pages)
    assert '<img src="https://example.invalid/cv.png">' in text
    assert '<style>@import url("https://example.invalid/style.css");</style>' in text
    assert calls == []


@pytest.mark.parametrize("url", [
    "https://example.invalid/cv.png", "http://169.254.169.254/latest/meta-data/",
    "file:///etc/passwd", "data:image/png;base64,aGVsbG8=",
])
def test_markdown_image_cannot_fetch_resources(monkeypatch, url):
    calls = []

    def forbidden_fetch(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("External resource accessed")

    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbidden_fetch)
    result = RequiredPdfRenderer().render(f"# Candidate\n\n![image]({url})")

    assert result.startswith(b"%PDF")
    assert calls == []
