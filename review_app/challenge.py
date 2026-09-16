"""
Zero-cookie, privacy-preserving anti-bot challenge for heavy submission forms
(GitHub issue #71).

Validates that submissions to heavy upload endpoints (/submit-band, /submit-release)
pass a local scene-knowledge verification challenge before large multipart payloads
are processed or stored on disk.
"""
from __future__ import annotations

import re
import unicodedata

# Canonical valid answers (normalized: lowercase, stripped of accents and non-alphanumeric chars)
# Covers historical names, Cyrillic, Latin, Latvian declensions, and common abbreviations.
VALID_ANSWERS = {
    "daugavpils",
    "даугавпилс",
    "двинск",
    "dvinsk",
    "dunaburg",
    "dünaburg",
    "дюнабург",
    "d-pils",
    "dpils",
    "дпилс",
    "daugavpili",
    "даугавпилсе",
    "daugavpilsi",
    "даугавпилсу",
    "даугавпилсом",
}

# Prefixes/suffixes commonly added by users
STRIP_PATTERNS = [
    r"^г(?:ород|\.)\s+",
    r"^city\s+of\s+",
    r"^в\s+",
    r"^in\s+",
    r"[,\s]+(латвия|латвии|latvija|latvia|latvijā|lv)$",
]


def normalize_answer(text: str) -> str:
    """Normalize user input: strip whitespace, lowercase, remove combining diacritics."""
    if not text:
        return ""
    text = text.strip().lower()
    for pattern in STRIP_PATTERNS:
        text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
    return text


def verify_challenge(answer: str | None) -> bool:
    """Check if the provided challenge answer matches the scene knowledge challenge.

    Returns True if valid, False otherwise.
    """
    if not answer or not isinstance(answer, str):
        return False

    norm = normalize_answer(answer)
    if not norm:
        return False

    if norm in VALID_ANSWERS:
        return True

    # Check without punctuation/hyphens (e.g. "d pils", "d-pils")
    clean = re.sub(r"[^\w\s-]", "", norm).strip()
    if clean in VALID_ANSWERS:
        return True

    # Check if any token matches
    tokens = re.split(r"[\s,;./-]+", norm)
    for token in tokens:
        if token in VALID_ANSWERS:
            return True

    # Diacritic removal (e.g. Dünaburg -> dunaburg)
    nfkd = "".join(c for c in unicodedata.normalize("NFKD", norm) if not unicodedata.combining(c))
    if nfkd in VALID_ANSWERS:
        return True

    for token in re.split(r"[\s,;./-]+", nfkd):
        if token in VALID_ANSWERS:
            return True

    return False
