"""Synthetic technique transcripts from rule-builder strategies, made by template (no model).

Gateway reply #SIW-lib-4 (2026-11-13): the answer comes out of the generation itself, so it cannot
drift from the transcript. Each condition of a validated rule-builder definition is said with one
of several phrasings meaning the same thing; vague remarks, sentences no rule can express and
chatter come from fixed lists; the script assigns the `[mm:ss]` times. Lines carry no punctuation,
as automatic Korean captions do, and long sentences are split over two lines.

An answer lists every condition with `terms`: groups of alternatives, all groups needing a hit in
a predicted statement's text. An alternative starting with `#` is a number matched exactly;
`~` marks a soft group (an indicator's period): needed for a full match, but a statement that
leaves it out is incomplete, not made up. `exclude` lists words of the opposite direction
(a statement holding one does not match, so "하향 돌파" is not "상향 돌파").
"""

from __future__ import annotations

import random
import re
from decimal import Decimal
from typing import Any

from services.backtest.strategy import validate_definition
from services.technique_cards.citations import Line, format_time, format_transcript

Terms = list[list[str]]

# Fixed vague remarks; at least two go into every synthetic item.
# `accept` is where the answer allows the remark, judged from the sentence alone by the v2 prompt
# rule (a number could settle it -> ambiguities, otherwise inexpressible), never from model
# output (gateway #SIW-lib-6). Only a remark that could go either way accepts both; the first
# entry is where the answer card lists it. `split` (gateway #SIW-lib-7) is set only on a remark
# holding a word that defines a testable condition by itself ("양봉" = close > open).
AMBIGUITIES: dict[str, dict[str, Any]] = {
    "volume_dry": {
        "accept": ["ambiguities"],
        "say": "거래량이 좀 줄면서 눌려 주는 게 좋아요",
        "text": "눌림 때 거래량이 '좀' 줄어야 한다 — 얼마나 줄어야 하는지 없음",
        "terms": [["거래량", "volume"], ["줄", "감소", "마르", "적", "decreas", "lower", "dry"]],
    },
    "strong_candle": {
        "accept": ["ambiguities"],
        "say": "그리고 양봉이 힘 있게 나와 줘야 됩니다",
        "text": "양봉이 '힘 있게' 나와야 한다 — 크기 기준 없음",
        "terms": [["양봉", "캔들", "봉", "candle"], ["힘", "강", "크", "strong", "large"]],
        # "양봉" is defined by its own word (close > open), so it may stand alone in entry.
        "split": {"field": "entry", "terms": [["양봉", "bullish", "종가가 시가"]]},
    },
    "near_line": {
        "accept": ["ambiguities"],
        "say": "선 근처까지 내려오면 그때부터 관심 갖고 보세요",
        "text": "선 '근처' — 얼마나 가까워야 하는지 없음",
        "terms": [["근처", "가까", "부근", "근접", "near", "close to"]],
    },
    "not_extended": {
        "accept": ["ambiguities"],
        "say": "너무 많이 오른 종목은 웬만하면 피하세요",
        "text": "'너무 많이 오른' 종목 제외 — 기준 없음",
        "terms": [["많이 오른", "급등", "과열", "너무", "extended", "overbought", "많이 상승"]],
    },
    "quick_exit": {
        "accept": ["ambiguities", "inexpressible"],
        "say": "흐름이 애매하다 싶으면 빨리 정리하는 게 낫습니다",
        "text": "'애매하면' 빨리 정리 — 무엇이 애매한지 없음",
        "terms": [["애매", "불확실", "모호", "unclear", "ambigu"]],
    },
    "volume_surge": {
        "accept": ["ambiguities"],
        "say": "돌파할 때 거래량이 충분히 터져 줘야 돼요",
        "text": "거래량이 '충분히' 터져야 한다 — 기준 없음",
        "terms": [
            ["거래량", "volume"],
            ["터", "충분", "많", "늘", "급증", "surge", "spike", "high", "increas"],
        ],
    },
}

# Fixed sentences said as rule-free advice; `accept` as above ("시장 분위기" could be an index
# condition, so it is an ambiguity).
INEXPRESSIBLE: dict[str, dict[str, Any]] = {
    "market_mood": {
        "accept": ["ambiguities"],
        "say": "이건 시장 분위기가 좋을 때만 하세요",
        "text": "시장 분위기가 좋을 때만",
        "terms": [["분위기", "시장 상황", "심리", "sentiment", "mood", "market condition"]],
    },
    "news_check": {
        "accept": ["inexpressible"],
        "say": "들어가기 전에 뉴스는 꼭 한 번 확인하시고요",
        "text": "진입 전 뉴스 확인",
        "terms": [["뉴스", "news", "재료"]],
    },
    "gut_feel": {
        "accept": ["inexpressible"],
        "say": "결국은 감이에요 차트를 많이 보셔야 됩니다",
        "text": "감·경험",
        "terms": [["감", "경험", "intuition", "feel", "experience"]],
    },
}

FILLER_OPEN = [
    "안녕하세요 오늘은 제가 자주 쓰는 매매법을 알려 드릴게요",
    "이거 진짜 많이들 물어보시더라고요",
    "자 그럼 바로 들어가 보겠습니다",
]
FILLER_MID = [
    "제가 예전에 이걸 모르고 손실을 많이 봤는데요",
    "여기까지 이해되셨죠",
    "이 부분이 제일 중요합니다",
]
FILLER_CLOSE = [
    "오늘 영상 도움 되셨으면 좋겠습니다",
    "구독이랑 좋아요 부탁드립니다",
]

ACTS = {
    "entry": ["삽니다", "매수 들어갑니다", "진입합니다"],
    "exit": ["팝니다", "매도합니다", "정리합니다"],
}

OP_PHRASES = {
    "cross_above": [
        "{L}{i} {R}{eul} 위로 돌파할 때 {act}",
        "{L}{i} {R}{eul} 상향 돌파하면 {act}",
        "{L}{i} {R} 위로 뚫고 올라가는 순간 {act}",
    ],
    "cross_below": [
        "{L}{i} {R}{eul} 아래로 깨면 {act}",
        "{L}{i} {R}{eul} 하향 돌파하면 {act}",
        "{L}{i} {R} 밑으로 내려가는 순간 {act}",
    ],
    "gt": ["{L}{i} {R}보다 위에 있을 때 {act}", "{L}{i} {R} 위에 있어야 {act}"],
    "gte": ["{L}{i} {R} 이상일 때 {act}"],
    "lt": ["{L}{i} {R}보다 아래에 있을 때 {act}", "{L}{i} {R} 밑에 있으면 {act}"],
    "lte": ["{L}{i} {R} 이하일 때 {act}"],
}
OP_TERMS = {
    "cross_above": ["상향", "위로", "돌파", "골든", "뚫", "올라", "넘", "cross", "above"],
    "cross_below": ["하향", "아래로", "이탈", "데드", "깨", "내려", "밑", "cross", "below"],
    "gt": ["위", "이상", "초과", "넘", "높", "above", "greater", "over"],
    "gte": ["위", "이상", "넘", "높", "above", "greater", "at least"],
    "lt": ["아래", "이하", "미만", "밑", "낮", "below", "less", "under"],
    "lte": ["아래", "이하", "밑", "낮", "below", "less", "at most"],
}
# Words that say the opposite direction: a statement holding one of them does not match.
OP_EXCLUDE = {
    "cross_above": ["하향", "아래로", "데드", "깨", "below"],
    "cross_below": ["상향", "위로", "골든", "above"],
    "gt": ["아래", "밑", "미만", "이하", "below", "under"],
    "gte": ["아래", "밑", "미만", "below", "under"],
    "lt": ["위에", "위로", "초과", "이상", "above", "over"],
    "lte": ["위에", "위로", "초과", "above", "over"],
}
OP_TEXT = {
    "cross_above": "상향 돌파",
    "cross_below": "하향 돌파",
    "gt": "> (위)",
    "gte": "≥ (이상)",
    "lt": "< (아래)",
    "lte": "≤ (이하)",
}

PRICE_NAMES = {
    "close": (["종가"], ["종가", "close", "마감"]),
    "open": (["시가"], ["시가", "open"]),
    "high": (["고가"], ["고가", "high", "고점"]),
    "low": (["저가"], ["저가", "low", "저점"]),
    "volume": (["거래량"], ["거래량", "volume"]),
}
PREVIOUS = ["전날", "전일", "어제", "직전", "이전", "previous", "prior", "前"]
BB_BANDS = {
    "upper": ("상단", ["상단", "upper", "위쪽"]),
    "lower": ("하단", ["하단", "lower", "아래쪽"]),
    "basis": ("중심선", ["중심", "중간", "basis", "middle"]),
}

# Final consonant of the sound of a digit or a Latin letter, for Korean particles.
_DIGIT_BATCHIM = set("013678")
_LETTER_BATCHIM = set("lmnr")


class SyntheticError(ValueError):
    pass


def _has_batchim(word: str) -> bool:
    for ch in reversed(word.strip()):
        if "가" <= ch <= "힣":
            return (ord(ch) - 0xAC00) % 28 != 0
        if ch.isdigit():
            return ch in _DIGIT_BATCHIM
        if ch.isalpha():
            return ch.lower() in _LETTER_BATCHIM
    return False


def _particles(word: str) -> dict[str, str]:
    batchim = _has_batchim(word)
    return {"i": "이" if batchim else "가", "eul": "을" if batchim else "를"}


def _number(value: object) -> str:
    """A rule value as said and matched: 2.0 -> 2, 0.50 -> 0.5."""
    number = Decimal(str(value)).normalize()
    text = format(number, "f")
    return text


def _operand(operand: dict[str, Any], rng: random.Random) -> tuple[str, str, Terms]:
    """(as said, as written in the answer, terms) for one operand."""
    if "value" in operand:
        number = _number(operand["value"])
        return number, number, [[f"#{number}"]]
    if "price" in operand:
        said_names, alternatives = PRICE_NAMES[operand["price"]]
        said = rng.choice(said_names)
        terms: Terms = [alternatives]
        written = said
        if operand.get("offset") == 1:
            # "직전 봉", never "전날": on a 5-minute chart one bar back is not yesterday.
            said, written = f"직전 봉 {said}", f"직전 봉 {said}"
            terms.append(PREVIOUS)
        elif "offset" in operand:
            raise SyntheticError("only an offset of one bar is phrased")
        return said, written, terms
    indicator = operand["indicator"]
    params = operand["params"]
    if "offset" in operand:
        raise SyntheticError("indicator offsets are not phrased")
    if indicator in ("sma", "ema", "rsi"):
        length = _number(params.get("length", {"sma": 9, "ema": 9, "rsi": 14}[indicator]))
        options = {
            "sma": [f"{length}일 이동평균선", f"{length}일선", f"{length}일 이평선"],
            "ema": [f"{length}일 지수이동평균", f"EMA {length}", f"{length}일 지수 이평"],
            "rsi": [f"RSI {length}", f"{length}일 RSI"],
        }[indicator]
        kind = {
            "sma": ["이동평균", "이평", "일선", "sma", "moving average"],
            "ema": ["지수", "ema", "exponential"],
            "rsi": ["rsi"],
        }[indicator]
        written = {"sma": f"SMA({length})", "ema": f"EMA({length})", "rsi": f"RSI({length})"}[
            indicator
        ]
        return rng.choice(options), written, [kind, [f"~#{length}"]]
    if indicator == "bb":
        length = _number(params.get("length", 20))
        mult = _number(params.get("mult", 2))
        band, band_terms = BB_BANDS[operand["output"]]
        said = f"{length}일 {mult} 표준편차 볼린저 밴드 {band}"
        written = f"BB({length}, {mult}) {band}"
        return (
            said,
            written,
            [["볼린저", "bollinger", "bb"], band_terms, [f"~#{length}"], [f"#{mult}"]],
        )
    raise SyntheticError(f"indicator {indicator!r} is not phrased")


def _comparison(
    condition: dict[str, Any], field: str, rng: random.Random
) -> tuple[str, str, Terms, list[str]]:
    if set(condition) != {"op", "left", "right"}:
        raise SyntheticError("only plain comparisons are phrased (no time windows or nesting)")
    left_said, left_written, left_terms = _operand(condition["left"], rng)
    right_said, right_written, right_terms = _operand(condition["right"], rng)
    template = rng.choice(OP_PHRASES[condition["op"]])
    said = template.format(
        L=left_said,
        R=right_said,
        act=rng.choice(ACTS[field]),
        **_particles_for(left_said, right_said),
    )
    written = f"{left_written} {OP_TEXT[condition['op']]} {right_written}"
    terms = [*left_terms, *right_terms, OP_TERMS[condition["op"]]]
    return said, written, terms, OP_EXCLUDE[condition["op"]]


def _particles_for(left: str, right: str) -> dict[str, str]:
    return {"i": _particles(left)["i"], "eul": _particles(right)["eul"]}


def _conditions(rule: dict[str, Any] | None) -> tuple[str, list[dict[str, Any]]]:
    if rule is None:
        return "all", []
    for mode in ("all", "any"):
        if mode in rule:
            return mode, list(rule[mode])
    return "all", [rule]


def _risk_statements(definition: dict[str, Any], rng: random.Random) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if definition.get("take_profit_pct") is not None:
        x = _number(definition["take_profit_pct"])
        out.append(
            {
                "field": "exit",
                "say": rng.choice(
                    [
                        f"익절은 {x}%에서 합니다",
                        f"{x}% 수익 나면 정리해요",
                        f"목표 수익은 {x}%입니다",
                    ]
                ),
                "text": f"익절 +{x}%",
                "terms": [[f"#{x}"], ["익절", "수익", "목표", "profit", "target"]],
            }
        )
    if definition.get("stop_loss_pct") is not None:
        x = _number(definition["stop_loss_pct"])
        out.append(
            {
                "field": "stop_loss",
                "say": rng.choice(
                    [
                        f"손절은 {x}%로 잡습니다",
                        f"{x}% 빠지면 무조건 손절이에요",
                        f"손실이 {x}% 나면 잘라요",
                    ]
                ),
                "text": f"손절 -{x}%",
                "terms": [[f"#{x}"], ["손절", "손실", "stop", "잘라", "빠지"]],
            }
        )
    atr = definition.get("stop_loss_atr")
    if atr is not None:
        length, mult = _number(atr["length"]), _number(atr["mult"])
        out.append(
            {
                "field": "stop_loss",
                "say": rng.choice(
                    [
                        f"손절은 진입가에서 ATR {length}의 {mult}배 아래에 둡니다",
                        f"ATR {length} 기준으로 {mult}배 빠지면 손절이에요",
                    ]
                ),
                "text": f"손절: 진입가 − ATR({length}) × {mult}",
                "terms": [["atr"], [f"~#{length}"], [f"#{mult}"]],
            }
        )
    trailing = definition.get("trailing_stop")
    if trailing is not None:
        if trailing["mode"] == "pct":
            v = _number(trailing["value"])
            say = rng.choice(
                [f"고점에서 {v}% 밀리면 따라가면서 정리합니다", f"트레일링 스탑 {v}% 걸어 둡니다"]
            )
            text, terms = (
                f"트레일링 스탑 {v}%",
                [[f"#{v}"], ["트레일", "추적", "trailing", "고점", "따라"]],
            )
        else:
            length, mult = _number(trailing["length"]), _number(trailing["mult"])
            say = f"ATR {length}의 {mult}배로 트레일링 스탑을 따라 올립니다"
            text = f"트레일링 스탑 ATR({length}) × {mult}"
            terms = [["atr"], [f"#{mult}"], ["트레일", "trailing", "추적", "따라"]]
        out.append({"field": "stop_loss", "say": say, "text": text, "terms": terms})
    return out


def _sizing(definition: dict[str, Any], rng: random.Random) -> dict[str, Any] | None:
    sizing = definition.get("sizing")
    if not sizing or sizing["mode"] != "percent_equity":
        return None
    v = _number(sizing["value"])
    return {
        "field": "position",
        "say": rng.choice([f"한 번에 계좌의 {v}%만 넣어요", f"비중은 자산의 {v}%로 합니다"]),
        "text": f"계좌의 {v}%",
        "terms": [[f"#{v}"], ["비중", "계좌", "자산", "equity", "position", "넣"]],
    }


def _strip(text: str) -> str:
    """Automatic captions carry no punctuation; % stays, it is said."""
    return re.sub(r"[.,!?~]", "", text).strip()


def _split(sentence: str) -> list[str]:
    """A long sentence over two caption lines, at the space nearest the middle."""
    if len(sentence) <= 28:
        return [sentence]
    spaces = [i for i, ch in enumerate(sentence) if ch == " "]
    if not spaces:
        return [sentence]
    cut = min(spaces, key=lambda i: abs(i - len(sentence) / 2))
    return [sentence[:cut], sentence[cut + 1 :]]


def build(spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """(transcript, answer) for one synthetic spec; the same spec always gives the same output."""
    definition = validate_definition(spec["rules"])
    if "entry_order" in definition or "exit_order" in definition:
        raise SyntheticError("limit and stop orders are not phrased")
    ambiguity_keys = list(spec["ambiguities"])
    if len(ambiguity_keys) < 2:
        raise SyntheticError("a synthetic item needs at least two vague remarks (#SIW-lib-4)")
    rng = random.Random(spec["seed"])

    # (field or kind, sentence, answer entry or None), in the order they are said.
    said: list[tuple[str, str, dict[str, Any] | None]] = []

    def say(kind: str, sentence: str, answer: dict[str, Any] | None = None) -> None:
        said.append((kind, _strip(sentence), answer))

    for line in FILLER_OPEN[: rng.randint(1, len(FILLER_OPEN))]:
        say("filler", line)
    say("timeframe", f"저는 {spec['timeframe']['say']} 기준으로 봅니다", spec["timeframe"])
    say("universe", f"종목은 {spec['universe']['say']} 위주로 합니다", spec["universe"])
    say("filler", rng.choice(FILLER_MID))

    vague: list[dict[str, Any]] = [dict(AMBIGUITIES[key], key=key) for key in ambiguity_keys]
    for field in ("entry", "exit"):
        mode, items = _conditions(definition.get(field))
        for index, condition in enumerate(items):
            sentence, written, terms, exclude = _comparison(condition, field, rng)
            if index > 0:
                sentence = ("그리고 동시에 " if mode == "all" else "아니면 ") + sentence
            condition_item: dict[str, Any] = {
                "field": field,
                "text": written,
                "terms": terms,
                "exclude": exclude,
            }
            say(field, sentence, condition_item)
            if field == "entry" and index == 0 and vague:
                remark = vague.pop(0)
                say("ambiguity", remark["say"], remark)
        if field == "entry":
            for key in spec["inexpressible"][:1]:
                say("inexpressible", INEXPRESSIBLE[key]["say"], dict(INEXPRESSIBLE[key], key=key))
    risk = _risk_statements(definition, rng)
    for risk_item in risk:
        if risk_item["field"] == "exit":
            say("exit", risk_item["say"], risk_item)
    say("filler", rng.choice(FILLER_MID))
    for remark in vague:
        say("ambiguity", remark["say"], remark)
    for risk_item in risk:
        if risk_item["field"] == "stop_loss":
            say("stop_loss", risk_item["say"], risk_item)
    sizing = _sizing(definition, rng)
    if sizing:
        say("position", sizing["say"], sizing)
    for key in spec["inexpressible"][1:]:
        say("inexpressible", INEXPRESSIBLE[key]["say"], dict(INEXPRESSIBLE[key], key=key))
    for line in FILLER_CLOSE:
        say("filler", line)

    lines: list[Line] = []
    clock = rng.randint(2, 6)
    answer: dict[str, Any] = {
        "source_id": spec["id"],
        "title": spec["title"],
        "conditions": [],
        "ambiguities": [],
        "inexpressible": [],
        "other": [],
    }
    for kind, sentence, item in said:
        start = clock
        for part in _split(sentence):
            lines.append(Line(clock, part))
            clock += max(2, len(part) // 6) + rng.randint(0, 2)
        if item is None:
            continue
        entry = {
            "text": item["text"],
            "terms": item["terms"],
            "exclude": item.get("exclude", []),
            "locator": format_time(start),
            "quote": sentence,
        }
        if kind in ("entry", "exit", "stop_loss"):
            answer["conditions"].append({"field": kind, **entry})
        elif kind in ("ambiguity", "inexpressible"):
            entry["accept"] = list(item["accept"])
            if "split" in item:
                entry["split"] = item["split"]
            answer[item["accept"][0]].append(entry)
        else:
            answer["other"].append({"field": kind, **entry})
    return format_transcript(lines), answer
