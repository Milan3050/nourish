"""Deterministic input and output guards. They run outside the LLM, so a prompt cannot turn them off."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from nip.security.pii import find_pii, redact

MAX_MESSAGE_CHARS = 4000

INJECTION_PATTERNS = [
    r"ignore (all |any |the )?(previous|prior|above|earlier) (instructions|rules|prompts?)",
    r"disregard (your|the|all) (instructions|rules|guidelines)",
    r"(reveal|show|print|repeat) (me )?(your|the) (system )?(prompt|instructions)",
    r"you are now (?!going)",
    r"\b(developer|god|dan|jailbreak) mode\b",
    r"pretend (you have|there are) no (rules|restrictions|limits)",
    r"</?(system|assistant|tool)>",
    r"act as (an? )?(unrestricted|uncensored)",
]
EATING_DISORDER_PATTERNS = [
    r"\b(purg(e|ing)|make myself (throw up|vomit|sick))\b",
    r"\blaxatives? (to|for) (lose|losing|weight)",
    r"\b(pro[- ]?ana|thinspo|meanspo)\b",
    r"\b(starv(e|ing) myself|not eat(ing)? for (days|a week))\b",
    r"\b(eat|eating|under|below|only) ([1-7]\d{2}|[1-9]\d) (kcal|calories|cal)\b",
    r"\bhate my body\b.*\b(eat|food|fat)\b",
]
CRISIS_PATTERNS = [r"\b(kill myself|suicid\w*|end my life|want to die|self[- ]harm)\b"]
MEDICAL_SCOPE_PATTERNS = [
    r"\b(stop|quit|reduce|change|replace) (taking )?(my )?(insulin|metformin|warfarin|medication|medicine|meds|pills?)\b",
    r"\b(diagnose|do i have|is it cancer|what disease)\b",
    r"\b(cure|reverse) (my )?(diabetes|cancer|kidney disease|hypertension)\b",
]


@dataclass
class InputCheck:
    text: str
    blocked: bool = False
    reason: str | None = None
    flags: list[str] = field(default_factory=list)


def _any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def check_input(message: str) -> InputCheck:
    msg = message.strip()
    if not msg:
        return InputCheck(text="", blocked=True, reason="empty")
    if len(msg) > MAX_MESSAGE_CHARS:
        return InputCheck(text=msg[:MAX_MESSAGE_CHARS], blocked=True, reason="too_long")
    flags: list[str] = []
    if find_pii(msg):
        flags.append("pii_redacted")
    clean = redact(msg)
    if _any(CRISIS_PATTERNS, clean):
        return InputCheck(text=clean, blocked=True, reason="crisis", flags=[*flags, "crisis"])
    if _any(INJECTION_PATTERNS, clean):
        flags.append("prompt_injection")
    if _any(EATING_DISORDER_PATTERNS, clean):
        flags.append("eating_disorder_risk")
    if _any(MEDICAL_SCOPE_PATTERNS, clean):
        flags.append("medical_scope")
    injection_only = "prompt_injection" in flags and not re.search(
        r"\b(eat|food|diet|meal|protein|calorie|kcal|nutrition|weight|plan|vegan|vegetarian)\w*", clean, re.I)
    if injection_only:
        return InputCheck(text=clean, blocked=True, reason="prompt_injection", flags=flags)
    return InputCheck(text=clean, flags=flags)


CRISIS_RESPONSE = (
    "I'm really sorry you're going through this. I can't help with this here, but you deserve support "
    "right now. In India you can call Tele-MANAS at 14416 (free, 24x7); in the US call or text 988. "
    "If you are in immediate danger, call your local emergency number. If you'd like, we can talk about "
    "food and nutrition another time."
)
BLOCKED_RESPONSES = {
    "empty": "Tell me about your goals or what you'd like help with.",
    "too_long": f"That message is too long. Please keep it under {MAX_MESSAGE_CHARS} characters.",
    "prompt_injection": "I can only help with nutrition and meal planning. What would you like to work on?",
    "crisis": CRISIS_RESPONSE,
}


def wrap_untrusted(text: str, source: str) -> str:
    """Retrieved text is data. Strip tag look-alikes and fence it so instructions inside are inert."""
    cleaned = re.sub(r"</?\s*(system|assistant|user|tool|untrusted_document)[^>]*>", "", text, flags=re.I)
    return f'<untrusted_document source="{source}">\n{cleaned}\n</untrusted_document>'


BANNED_OUTPUT_PATTERNS = [
    (r"\b(stop|discontinue|reduce|skip) (taking )?(your )?(insulin|medication|medicine|meds|metformin|warfarin)\b",
     "advises changing medication"),
    (r"\b(cures?|reverses?) (your )?(diabetes|cancer|hypertension|kidney disease)\b", "claims a cure"),
    (r"\bguaranteed\b", "makes guarantees"),
    (r"\byou (have|are suffering from) [a-z ]{3,30}(disease|disorder|syndrome)\b", "diagnoses"),
]


def check_output(text: str) -> list[str]:
    return [why for pat, why in BANNED_OUTPUT_PATTERNS if re.search(pat, text, re.I)]
