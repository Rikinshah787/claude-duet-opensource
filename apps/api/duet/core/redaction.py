"""Sensitive-information classifier and egress gate.

An assistant that reads your mail and sends messages for you is constantly
deciding where information should go. This module is the check on that decision.

Two tiers, with different rules:

* **STRICT** - payment card numbers, government identifiers, bank accounts,
  private keys, live API tokens. These are never written outbound, no matter
  what the user or a prompt asks for. There is no consent flag that unlocks
  them, because the most common way they leave is an instruction that *looks*
  like it came from the user.

* **GUARDED** - health details, personal finances, compensation, home address.
  These pass only when the user explicitly asked for this specific send.

`redact()` is the log-safe scrubber and is applied to every audit detail.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Tier(str, Enum):
    STRICT = "strict"
    GUARDED = "guarded"


@dataclass(frozen=True)
class Detector:
    name: str
    tier: Tier
    pattern: re.Pattern
    label: str
    validator: str | None = None


def _luhn(digits: str) -> bool:
    nums = [int(d) for d in digits if d.isdigit()]
    if len(nums) < 13:
        return False
    checksum, parity = 0, len(nums) % 2
    for i, n in enumerate(nums):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


DETECTORS: tuple[Detector, ...] = (
    Detector(
        "payment_card",
        Tier.STRICT,
        re.compile(r"\b(?:\d[ -]*?){13,19}\b"),
        "a payment card number",
        validator="luhn",
    ),
    Detector("us_ssn", Tier.STRICT, re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "a Social Security number"),
    Detector(
        "passport",
        Tier.STRICT,
        re.compile(r"\b(?:passport\s*(?:no\.?|number|#)\s*[:=]?\s*)([A-Z0-9]{6,9})\b", re.I),
        "a passport number",
    ),
    Detector(
        "bank_account",
        Tier.STRICT,
        re.compile(r"\b(?:routing|account)\s*(?:no\.?|number|#)\s*[:=]?\s*(\d{8,17})\b", re.I),
        "a bank account or routing number",
    ),
    Detector(
        "private_key",
        Tier.STRICT,
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
        "a private key",
    ),
    Detector(
        "api_token",
        Tier.STRICT,
        re.compile(
            r"\b(?:sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}"
            r"|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,})\b"
        ),
        "a live API credential",
    ),
    Detector(
        "health",
        Tier.GUARDED,
        re.compile(
            r"\b(diagnos(?:is|ed)|prescription|prescribed|medication|biopsy|oncolog\w*|"
            r"HIV|chemotherapy|psychiatric|therapist|mental health|blood test(?: results?)?|"
            r"medical record)\b",
            re.I,
        ),
        "health information",
    ),
    Detector(
        "personal_finance",
        Tier.GUARDED,
        re.compile(
            r"\b(salary|compensation|base pay|bonus structure|net worth|credit score|"
            r"bank balance|mortgage balance|tax return|W-2|1099)\b",
            re.I,
        ),
        "personal financial information",
    ),
    Detector(
        "home_address",
        Tier.GUARDED,
        re.compile(
            r"\b\d{1,5}\s+[A-Z][a-z]+(?:\s[A-Z][a-z]+)*\s"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd)\b"
        ),
        "a home address",
    ),
)

SECRET_KEYS = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|credential|private[_-]?key"
    r"|access[_-]?token|refresh[_-]?token|client[_-]?secret|cookie|session)",
    re.I,
)


@dataclass
class Detection:
    detector: str
    tier: Tier
    label: str
    excerpt: str


def classify(text: str) -> list[Detection]:
    """Find sensitive content. Order is stable so decisions are reproducible."""
    if not text:
        return []
    found: list[Detection] = []
    for det in DETECTORS:
        for match in det.pattern.finditer(text):
            raw = match.group(0)
            if det.validator == "luhn" and not _luhn(raw):
                continue
            found.append(
                Detection(
                    detector=det.name,
                    tier=det.tier,
                    label=det.label,
                    excerpt=_mask(raw),
                )
            )
            break  # one hit per detector is enough to make the decision
    return found


def _mask(value: str) -> str:
    v = value.strip()
    if len(v) <= 8:
        return "*" * len(v)
    return f"{v[:2]}{'*' * (len(v) - 6)}{v[-4:]}"


@dataclass
class EgressDecision:
    allowed: bool
    reason: str
    detections: list[Detection]

    @property
    def blocked_labels(self) -> list[str]:
        return sorted({d.label for d in self.detections})


def check_egress(text: str, *, destination: str, user_directed: bool = False) -> EgressDecision:
    """Gate outbound content.

    `user_directed` means the user explicitly asked for *this* send in their own
    words. It relaxes GUARDED only. STRICT never passes - that is the point of
    the tier.
    """
    detections = classify(text)
    strict = [d for d in detections if d.tier is Tier.STRICT]
    guarded = [d for d in detections if d.tier is Tier.GUARDED]

    if strict:
        labels = ", ".join(sorted({d.label for d in strict}))
        return EgressDecision(
            allowed=False,
            reason=(
                f"Blocked: this would send {labels} to {destination}. "
                "That category is never sent, including when asked."
            ),
            detections=strict,
        )

    if guarded and not user_directed:
        labels = ", ".join(sorted({d.label for d in guarded}))
        return EgressDecision(
            allowed=False,
            reason=(
                f"Held: this contains {labels}. Confirm you want it sent to "
                f"{destination} and it will go."
            ),
            detections=guarded,
        )

    return EgressDecision(allowed=True, reason="No sensitive content detected.", detections=[])


def redact(obj: Any, _depth: int = 0) -> Any:
    """Recursively scrub values before they reach a log or a model prompt."""
    if _depth > 12:
        return "<truncated>"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and SECRET_KEYS.search(k):
                out[k] = "<redacted>"
            else:
                out[k] = redact(v, _depth + 1)
        return out
    if isinstance(obj, (list, tuple)):
        return [redact(v, _depth + 1) for v in obj]
    if isinstance(obj, str):
        text = obj
        for det in DETECTORS:
            if det.tier is not Tier.STRICT:
                continue
            if det.validator == "luhn":
                text = det.pattern.sub(
                    lambda m: _mask(m.group(0)) if _luhn(m.group(0)) else m.group(0), text
                )
            else:
                text = det.pattern.sub(lambda m: _mask(m.group(0)), text)
        return text
    return obj
