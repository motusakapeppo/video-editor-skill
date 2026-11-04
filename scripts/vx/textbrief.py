"""vx.textbrief — what a prompt asks to write on screen, in seconds and pixels.

The ``auto`` path receives a single string: the brief. This module reads that
string for the only things typography can honour without inventing them: the
hook sentence and when it enters, the call to action and when it enters, the
requested family, body size and style. Everything else is absent, and absence
is a value (``hook_text == ""``), never a default sentence.

The parser is deliberately literal: a sentence has to be named (``hook:`` or
``cta:``) or quoted, and a number has to carry its unit or its field. A brief
that only asks for ``sottotitoli`` keeps the existing caption path untouched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

__all__ = ["BriefText", "parse_brief_text", "DEFAULT_HOOK_AT"]

#: Un hook che entra a 0.10 s e' il riferimento misurato sul reel giudicato
#: buono; e' un default, e il brief puo' sempre dire un altro secondo.
DEFAULT_HOOK_AT = 0.10
DEFAULT_HOOK_DUR = 3.0
DEFAULT_CTA_DUR = 3.0

_TEXT_STOP = (
    r"(?=\s*(?:[,;|\n]\s*)?(?:"
    r"cta|call\s*to\s*action|font|size|corpo|stile|style|"
    r"hook\s*(?:at|a|durata|dur|duration|per)|"
    r"cta\s*(?:at|a|durata|dur|duration|per)"
    r")\b|\s*[;|\n]|$)"
)
_HOOK_TEXT = re.compile(
    r"\bhook(?:_text)?\b\s*(?:text|testo)?\s*[:=]\s*(.+?)" + _TEXT_STOP,
    re.IGNORECASE,
)
_CTA_TEXT = re.compile(
    r"\b(?:cta(?:_text)?|call\s*to\s*action)\b\s*[:=]\s*(.+?)" + _TEXT_STOP,
    re.IGNORECASE,
)
_QUOTED_HOOK = re.compile(
    r"\bhook(?:_text)?\b\s*(?:text|testo)?\s*[:=]?\s*[\"“']([^\"”']+)[\"”']",
    re.IGNORECASE,
)
_QUOTED_CTA = re.compile(
    r"\b(?:cta(?:_text)?|call\s*to\s*action)\b\s*[:=]?\s*[\"“']([^\"”']+)[\"”']",
    re.IGNORECASE,
)
_HOOK_AT = re.compile(
    r"(?:\bhook_at\b|\bhook\b[^.;|\n]{0,24}?\b(?:at|a))\s*[:=]?\s*"
    r"(\d+(?:[.,]\d+)?)\s*s?\b",
    re.IGNORECASE,
)
_CTA_AT = re.compile(
    r"(?:\bcta_at\b|\bcta\b[^.;|\n]{0,24}?\b(?:at|a))\s*[:=]?\s*"
    r"(\d+(?:[.,]\d+)?)\s*s?\b",
    re.IGNORECASE,
)
_HOOK_DUR = re.compile(
    r"(?:\bhook_dur\b|\bhook\b[^.;|\n]{0,24}?\b(?:durata|dur|duration|per))"
    r"\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*s?\b",
    re.IGNORECASE,
)
_CTA_DUR = re.compile(
    r"(?:\bcta_dur\b|\bcta\b[^.;|\n]{0,24}?\b(?:durata|dur|duration|per))"
    r"\s*[:=]?\s*(\d+(?:[.,]\d+)?)\s*s?\b",
    re.IGNORECASE,
)
_FONT = re.compile(r"\bfont\s+([A-Za-z][A-Za-z0-9_-]*)", re.IGNORECASE)
_SIZE = re.compile(
    r"\b(?:(?:size|corpo|font\s+size)\s*[:=]?\s*(\d{2,3})|(\d{2,3})\s*px)\b",
    re.IGNORECASE,
)
_STYLE = re.compile(
    r"\b(?:stile|style)\s*[:=]?\s*([A-Za-z][A-Za-z0-9_-]*)", re.IGNORECASE
)
_HOOK_WORD = re.compile(r"\bhook\b|\bgancio\b", re.IGNORECASE)
_CTA_WORD = re.compile(r"\bcta\b|\bcall\s*to\s*action\b", re.IGNORECASE)


def _seconds(value: str) -> Optional[float]:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _clean(text: str) -> str:
    """One line, no surrounding punctuation: the sentence the writer gets."""
    value = " ".join(str(text or "").split()).strip()
    value = value.strip(" \t\"“”'.,;|")
    return value


@dataclass(frozen=True)
class BriefText:
    """The textual requests found in the brief, with their second and pixels."""

    hook_text: str = ""
    hook_at: float = DEFAULT_HOOK_AT
    hook_dur: float = DEFAULT_HOOK_DUR
    cta_text: str = ""
    cta_at: Optional[float] = None
    cta_dur: float = DEFAULT_CTA_DUR
    font: str = ""
    size: Optional[int] = None
    style: str = ""
    asks_hook: bool = False
    asks_cta: bool = False
    reason: str = ""

    @property
    def asks_text(self) -> bool:
        return bool(self.hook_text or self.cta_text or self.asks_hook or self.asks_cta)

    def to_dict(self) -> dict:
        return {
            "hook_text": self.hook_text,
            "hook_at": self.hook_at,
            "hook_dur": self.hook_dur,
            "cta_text": self.cta_text,
            "cta_at": self.cta_at,
            "cta_dur": self.cta_dur,
            "font": self.font,
            "size": self.size,
            "style": self.style,
            "asks_hook": self.asks_hook,
            "asks_cta": self.asks_cta,
            "asks_text": self.asks_text,
            "reason": self.reason,
        }


def parse_brief_text(prompt: object) -> BriefText:
    """Read the brief once; every field is either found or empty, never guessed."""
    text = str(prompt or "")
    hook_match = _HOOK_TEXT.search(text) or _QUOTED_HOOK.search(text)
    cta_match = _CTA_TEXT.search(text) or _QUOTED_CTA.search(text)
    hook_text = _clean(hook_match.group(1)) if hook_match else ""
    cta_text = _clean(cta_match.group(1)) if cta_match else ""

    asks_hook = bool(hook_text) or bool(_HOOK_WORD.search(text))
    asks_cta = bool(cta_text) or bool(_CTA_WORD.search(text))

    hook_at = _seconds(_HOOK_AT.search(text).group(1)) if _HOOK_AT.search(text) else None
    hook_dur = (
        _seconds(_HOOK_DUR.search(text).group(1)) if _HOOK_DUR.search(text) else None
    )
    cta_at = _seconds(_CTA_AT.search(text).group(1)) if _CTA_AT.search(text) else None
    cta_dur = (
        _seconds(_CTA_DUR.search(text).group(1)) if _CTA_DUR.search(text) else None
    )
    font_match = _FONT.search(text)
    size_match = _SIZE.search(text)
    size: Optional[int] = None
    if size_match:
        parsed = int(size_match.group(1) or size_match.group(2))
        # Un numero fuori da questo intervallo e' geometria (1080 px), non corpo.
        size = parsed if 8 <= parsed <= 400 else None
    style_match = _STYLE.search(text)
    style = _clean(style_match.group(1)) if style_match else ""
    if not style:
        for candidate in ("karaoke", "clean"):
            if re.search(rf"\b{candidate}\b", text, re.IGNORECASE):
                style = candidate
                break

    reason = ""
    if not (asks_hook or asks_cta):
        reason = (
            "il brief non chiede hook ne' CTA: nessun ASS di tipografia "
            "(i sottotitoli restano il percorso esistente)"
        )
    return BriefText(
        hook_text=hook_text,
        hook_at=max(0.0, hook_at if hook_at is not None else DEFAULT_HOOK_AT),
        hook_dur=max(0.2, hook_dur if hook_dur is not None else DEFAULT_HOOK_DUR),
        cta_text=cta_text,
        cta_at=cta_at,
        cta_dur=max(0.2, cta_dur if cta_dur is not None else DEFAULT_CTA_DUR),
        font=font_match.group(1) if font_match else "",
        size=size,        style=style,
        asks_hook=asks_hook,
        asks_cta=asks_cta,
        reason=reason,
    )
