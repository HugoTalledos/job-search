"""ResumeSource port reading the base resume from a local file (.md, .txt or .pdf)."""

from __future__ import annotations

from pathlib import Path


class FileResumeSource:
    def __init__(self, path: Path) -> None:
        self.path = path

    def read(self) -> str:
        if not self.path.exists():
            raise FileNotFoundError(f"Resume not found at {self.path}. Put your CV there (Markdown, .txt or .pdf).")
        if self.path.suffix.lower() == ".pdf":
            from pypdf import PdfReader

            return "\n".join(page.extract_text() or "" for page in PdfReader(self.path).pages)
        return self.path.read_text()
