"""Driven adapters for the candidate's resume (source) and resume rendering."""

from .file_resume_source import FileResumeSource
from .markdown_renderer import write_outputs

__all__ = ["FileResumeSource", "write_outputs"]
