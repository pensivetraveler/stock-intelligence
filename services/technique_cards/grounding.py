"""Grounding check for scoring rules v2 (gateway #SIW-lib-12): is a condition backed by its quote?

A card's entry / exit / stop-loss statement is grounded when it has at least one passing citation
and every number and every indicator name it states appears in the text around its passing
citations (±15 s). Numbers said as words count: "fifty", "zero point zero five", "half",
"두 배", "절반". This catches what rules v1 tried to catch with word lists — a number or an
indicator taken from the author's code or invented (EMA 9·21, ATR 14, "10-period EMA") — without
needing the answer card to foresee it.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from services.technique_cards.citations import (
    WINDOW_SECONDS,
    Line,
    check_citation,
    locator_seconds,
    normalise,
    window_text,
)

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}  # fmt: skip
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90,
}  # fmt: skip
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
_SINGLE = {"half": Decimal("0.5"), "twice": Decimal(2), "double": Decimal(2)}
# Native Korean counting words, as whole tokens or with a counter after them ("두 배", "세번").
_KOREAN = {
    "하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4, "다섯": 5,
    "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "스물": 20, "스무": 20,
}  # fmt: skip
_KOREAN_COUNTERS = ("", "배", "번", "개", "가지", "단위", "칸", "봉", "일", "주", "달")
_KOREAN_HALF = ("절반", "반절")
_AMBIGUOUS = {"한", "두", "세", "네"}

_DIGITS = re.compile(r"(?<![\d.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\d.])\d+(?:\.\d+)?")

# Indicator families: names that mark a statement as using it, and names that ground it.
_INDICATORS: dict[str, tuple[list[str], list[str]]] = {
    "moving average": (
        ["moving average", "이동평균", "이평", "ema", "sma", "wma"],
        ["moving average", "average", "이동평균", "이평", "일선", "ema", "sma", "exponential",
         "지수", "평균선"],
    ),
    "ema": (["ema", "exponential", "지수이동평균", "지수 이동평균", "지수이평"],
            ["ema", "exponential", "지수"]),
    "sma": (["sma", "simple moving average", "단순이동평균"],
            ["sma", "simple", "단순", "moving average", "이동평균"]),
    "rsi": (["rsi", "relative strength", "상대강도"], ["rsi", "relative strength", "상대강도"]),
    "atr": (["atr", "average true range", "평균진폭", "평균 진폭"],
            ["atr", "average true range", "true range", "평균진폭", "평균 진폭", "변동폭", "n"]),
    "macd": (["macd"], ["macd"]),
    "adx": (["adx", "dmi", "+di", "-di", "directional"], ["adx", "dmi", "di", "directional"]),
    "bollinger": (["bollinger", "볼린저"],
                  ["bollinger", "볼린저", "band", "밴드", "midline", "middle line", "centerline",
                   "center line", "중심선", "중간선"]),
    "donchian": (["donchian", "돈치안"],
                 ["donchian", "돈치안", "channel", "채널", "highest", "lowest", "최고가",
                  "최저가"]),
    "stochastic": (["stochastic", "스토캐스틱"], ["stochastic", "스토캐스틱"]),
    "vwap": (["vwap"], ["vwap"]),
    "obv": (["obv", "on balance volume"], ["obv", "on balance"]),
    "cci": (["cci", "commodity channel"], ["cci", "commodity"]),
    "ichimoku": (["ichimoku", "일목"], ["ichimoku", "일목"]),
}  # fmt: skip
_LATIN_SHORT = re.compile(r"^[a-z+\-]{2,5}$")


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace(",", "")).normalize()
    except InvalidOperation:
        return None


def _english_numbers(text: str) -> set[Decimal]:
    """Values said in English words, each run and its parts: "two hundred" -> {2, 100, 200}."""
    words = re.findall(r"[a-z]+", text.casefold())
    found: set[Decimal] = set()
    i = 0
    while i < len(words):
        word = words[i]
        if word in _SINGLE:
            found.add(_SINGLE[word])
        if word not in _UNITS and word not in _TENS and word not in _SCALES:
            i += 1
            continue
        total, current = 0, 0
        fraction = ""
        j = i
        while j < len(words):
            w = words[j]
            if w in _UNITS:
                current += _UNITS[w]
                found.add(Decimal(_UNITS[w]))
            elif w in _TENS:
                current += _TENS[w]
                found.add(Decimal(_TENS[w]))
            elif w in _SCALES:
                current = max(current, 1) * _SCALES[w]
                if _SCALES[w] >= 1000:
                    total, current = total + current, 0
            elif w == "and" and words[j + 1 : j + 3] == ["a", "half"]:
                fraction = "5"  # "one and a half" -> 1.5
                j += 3
                break
            elif (
                w == "and"
                and j + 1 < len(words)
                and (words[j + 1] in _UNITS or words[j + 1] in _TENS)
            ):
                pass
            elif w == "point":
                k = j + 1
                while k < len(words) and words[k] in _UNITS and _UNITS[words[k]] < 10:
                    fraction += str(_UNITS[words[k]])
                    k += 1
                j = k
                break
            else:
                break
            j += 1
        value = Decimal(total + current)
        if fraction:
            value = Decimal(f"{total + current}.{fraction}")
        found.add(value.normalize())
        i = max(j, i + 1)
    return found


def _korean_numbers(text: str) -> set[Decimal]:
    """Native Korean counts. 한·두·세·네 are also ordinary words ("익절을 한 뒤"), so alone
    they count only before a counter word ("한 번", "두 배")."""
    found: set[Decimal] = set()
    tokens = re.findall(r"[가-힣]+", text)
    for i, token in enumerate(tokens):
        if any(token.startswith(half) for half in _KOREAN_HALF):
            found.add(Decimal("0.5"))
        for word, value in _KOREAN.items():
            if word in _AMBIGUOUS:
                following = tokens[i + 1] if i + 1 < len(tokens) else ""
                attached = any(token == word + c for c in _KOREAN_COUNTERS[1:])
                spaced = token == word and any(
                    following.startswith(c) for c in _KOREAN_COUNTERS[1:]
                )
                if attached or spaced:
                    found.add(Decimal(value))
                continue
            if any(token == word + counter for counter in _KOREAN_COUNTERS):
                found.add(Decimal(value))
    return found


def numbers(text: str) -> set[Decimal]:
    """Every number a text states, in digits or in words (English, native Korean)."""
    found = {d for d in (_decimal(m) for m in _DIGITS.findall(text)) if d is not None}
    return found | _english_numbers(text) | _korean_numbers(text)


def _mentions(name: str, text: str) -> bool:
    raw = text.casefold()
    if _LATIN_SHORT.match(name):
        return re.search(rf"(?<![a-z]){re.escape(name)}s?(?![a-z])", raw) is not None
    return normalise(name) in normalise(text)


def indicators(text: str) -> set[str]:
    return {
        family
        for family, (marks, _) in _INDICATORS.items()
        if any(_mentions(m, text) for m in marks)
    }


def grounds(family: str, window: str) -> bool:
    """Is an indicator family said in this text (its names, or letters read one by one)?"""
    return _grounds(family, window)


def _grounds(family: str, window: str) -> bool:
    raw = window.casefold()
    folded = normalise(window)
    for name in _INDICATORS[family][1]:
        if len(name) <= 2:
            if re.search(rf"(?<![a-z]){re.escape(name)}(?![a-z])", raw):
                return True
        elif normalise(name) in folded or (" " in name and name in raw):
            return True
    # Letters read out one by one in captions ("A T R", "E M A").
    spaced = re.sub(r"(?<=\b[a-z]) (?=[a-z]\b)", "", raw)
    return any(
        len(n) <= 4 and re.search(rf"(?<![a-z]){re.escape(n)}(?![a-z])", spaced)
        for n in _INDICATORS[family][1]
    )


def check_statement(statement: dict[str, object], lines: list[Line]) -> str:
    """Why a condition statement is not grounded, or "" when it is."""
    citations = statement.get("citations") or []
    windows = []
    for citation in citations if isinstance(citations, list) else []:
        if not isinstance(citation, dict):
            continue
        locator, quote = str(citation.get("locator", "")), str(citation.get("quote", ""))
        if check_citation(lines, locator, quote, WINDOW_SECONDS).ok:
            at = locator_seconds(locator)
            if at is not None:
                windows.append(window_text(lines, at, WINDOW_SECONDS))
    if not windows:
        return "no passing citation"
    text = str(statement.get("text", ""))
    window = " ".join(windows)
    missing_numbers = numbers(text) - numbers(window)
    if missing_numbers:
        shown = sorted(format(n, "f") for n in missing_numbers)
        return "number not in the cited source: " + ", ".join(shown)
    missing = [family for family in sorted(indicators(text)) if not _grounds(family, window)]
    if missing:
        return "indicator not in the cited source: " + ", ".join(missing)
    return ""
