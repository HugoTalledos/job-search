"""Stylesheet shared by the CV PDF renderer."""

from __future__ import annotations

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
