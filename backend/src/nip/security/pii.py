"""Deterministic PII redaction applied before text reaches an LLM, a log line or a trace.

Health details the user shares on purpose (age, weight, conditions) are needed for the task and
are passed through structured fields; identifiers that are never needed are stripped.
"""

from __future__ import annotations

import re

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")),
    ("AADHAAR", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("SSN", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,16}\b")),
    ("PHONE", re.compile(r"(?<!\w)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\w)")),  # Indian mobile
    ("PHONE", re.compile(r"(?<!\w)(?:\+?\d{1,3}[\s-]?)?(?:\(?\d{3,5}\)?[\s-]?)\d{3,4}[\s-]?\d{4}(?!\w)")),
]


def redact(text: str) -> str:
    for label, pat in PATTERNS:
        text = pat.sub(f"[{label}]", text)
    return text


def find_pii(text: str) -> list[str]:
    return [label for label, pat in PATTERNS if pat.search(text)]
