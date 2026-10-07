"""One place that turns an event name into a safe filename.

Event names contain spaces and, for control windows, a colon
("PLACEBO 2026-10-06 10:30 IST"). On Windows/NTFS a colon in a filename is
not rejected -- everything after it becomes a hidden alternate data stream
of a file named for the text BEFORE the colon, leaving a visible 0-byte
file and no usable output (BUGS.md BUG-8: the capture log and charts for
the first control window were silently lost this way).
"""
from __future__ import annotations

import re


def safe_filename(name: str) -> str:
    """Letters, digits, '.', '_' and '-' only; every other run of characters
    becomes one underscore."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("_")
