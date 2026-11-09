"""Keyword rules that tag a news article with catalyst categories (ADR 0052 §3, step 1).

Only the headline and the summary are read. A rule matches whole words or phrases,
ignoring case; one article can carry several categories (a merger often says
"agreement" too). An article that matches nothing is "none": no catalyst found by these
rules, which is not the same as no catalyst. The rule set has a version and a checksum
so a screening result says exactly which words were used. No LLM is involved.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from functools import cache

RULES_VERSION = "news-rules-1"

#: (category, Korean label, phrases). Order is the order categories are reported in.
RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "offering",
        "유상증자·공모",
        (
            "public offering",
            "registered direct offering",
            "registered direct",
            "private placement",
            "pipe financing",
            "underwritten offering",
            "priced offering",
            "announces pricing",
            "prices offering",
            "at-the-market offering",
            "atm offering",
            "atm program",
            "equity offering",
            "shelf registration",
            "proposed offering",
            "warrant inducement",
            "securities purchase agreement",
            "equity line",
            "convertible notes offering",
        ),
    ),
    (
        "earnings",
        "실적",
        (
            "earnings",
            "quarterly results",
            "financial results",
            "first quarter",
            "second quarter",
            "third quarter",
            "fourth quarter",
            "full year results",
            "fiscal year results",
            "q1 results",
            "q2 results",
            "q3 results",
            "q4 results",
            "eps",
            "revenue guidance",
            "raises guidance",
            "lowers guidance",
            "preliminary revenue",
        ),
    ),
    (
        "clinical_fda",
        "임상·FDA",
        (
            "fda",
            "clinical trial",
            "phase 1",
            "phase 2",
            "phase 3",
            "phase i",
            "phase ii",
            "phase iii",
            "topline",
            "top-line",
            "pdufa",
            "ind clearance",
            "breakthrough therapy",
            "fast track designation",
            "orphan drug designation",
            "biologics license application",
            "new drug application",
            "ema approval",
        ),
    ),
    (
        "contract_order",
        "계약·수주",
        (
            "contract",
            "awarded",
            "purchase order",
            "supply agreement",
            "licensing agreement",
            "license agreement",
            "distribution agreement",
            "strategic partnership",
            "partnership",
            "collaboration",
            "memorandum of understanding",
            "letter of intent",
        ),
    ),
    (
        "merger_acquisition",
        "인수합병",
        (
            "merger",
            "to acquire",
            "acquisition",
            "acquires",
            "to be acquired",
            "takeover",
            "buyout",
            "tender offer",
            "definitive agreement",
            "business combination",
            "go-private",
            "take private",
        ),
    ),
    (
        "reverse_split",
        "역분할",
        (
            "reverse stock split",
            "reverse split",
            "share consolidation",
        ),
    ),
    (
        "listing_notice",
        "상장 유지 통지",
        (
            "minimum bid price",
            "bid price requirement",
            "listing rule",
            "listing requirement",
            "deficiency notice",
            "deficiency letter",
            "notice of delisting",
            "delisting notice",
            "delisting",
            "regain compliance",
            "regained compliance",
            "noncompliance",
            "non-compliance",
            "hearings panel",
        ),
    ),
)
NONE = "none"
LABELS = {category: label for category, label, _ in RULES} | {NONE: "재료 없음(규칙상)"}


def rules_checksum() -> str:
    canonical = json.dumps(RULES, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


@cache
def _patterns() -> tuple[tuple[str, re.Pattern[str]], ...]:
    built = []
    for category, _, phrases in RULES:
        words = sorted(phrases, key=len, reverse=True)
        alternation = "|".join(re.escape(w).replace(r"\ ", r"[\s-]+") for w in words)
        built.append((category, re.compile(rf"(?<![\w-])(?:{alternation})(?![\w-])", re.I)))
    return tuple(built)


@dataclass(frozen=True)
class Classification:
    categories: tuple[str, ...]  # (NONE,) when nothing matched
    #: The phrases that matched, by category, lower-cased, in order of appearance.
    matches: dict[str, tuple[str, ...]]

    @property
    def material(self) -> bool:
        return self.categories != (NONE,)


def classify(headline: str, summary: str) -> Classification:
    text = f"{headline}\n{summary}"
    matches: dict[str, tuple[str, ...]] = {}
    for category, pattern in _patterns():
        found = tuple(dict.fromkeys(m.group(0).lower() for m in pattern.finditer(text)))
        if found:
            matches[category] = found
    if not matches:
        return Classification((NONE,), {})
    return Classification(tuple(matches), matches)
