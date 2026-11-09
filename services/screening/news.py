"""Screening check B — news catalysts (ADR 0052 §3), keyword rules only.

`news_check` returns the parts of one `screening_check` document (status, result,
versions, warnings, sources) for one instrument and cutoff; the route adds the envelope.
It reads the Alpaca News articles created in the look-back window up to the cutoff, tags
each with `news_rules`, and lists source records, versions and what could not be
confirmed. Articles created after the cutoff are never read into the result. The check
places no order and changes no entry condition; choosing an entry preset from the
catalyst (S5) is a separate, user-defined table.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from services.agents.evidence import Fact
from services.market_data.instruments import parse_instrument
from services.public_sources import alpaca_news
from services.public_sources.alpaca_news import AlpacaNews, NewsArticle
from services.public_sources.cache import utc_text
from services.public_sources.http import SourceConfigError, SourceError
from services.screening import news_rules
from services.screening.news_rules import LABELS, NONE, RULES, classify

DEFAULT_LOOKBACK_HOURS = 72
MAX_LOOKBACK_HOURS = 24 * 30

ALWAYS_UNCERTAIN = (
    "Catalyst categories come from fixed keyword rules on the headline and summary only; "
    "a missed or wrong category is possible, and 'none' means no rule matched, not that "
    "there is no catalyst.",
    "Alpaca documents created_at only as the article's creation time; it is used as the "
    "publication time (unverified).",
    "Alpaca News carries some feeds (for example Benzinga and fiscal.ai), not all news; "
    "SEC filings are a separate check.",
)


def _hours(delta: timedelta) -> str:
    return f"{delta.total_seconds() / 3600:.1f}"


def _base(start: datetime, lookback_hours: int) -> dict[str, Any]:
    return {
        "status": "error",
        "result": {
            "reason": None,
            "window_start_utc": utc_text(start),
            "lookback_hours": lookback_hours,
            "categories": [],
            "articles": [],
            "facts": [],
            "missing": [],
            "uncertainty": list(ALWAYS_UNCERTAIN),
        },
        "versions": {
            "news_rules": news_rules.RULES_VERSION,
            "news_rules_sha256": news_rules.rules_checksum(),
            "parser": alpaca_news.PARSER_VERSION,
        },
        "warnings": [],
        "sources": {"source_id": alpaca_news.ALPACA_NEWS, "records": []},
    }


def _article_document(article: NewsArticle, cutoff: datetime) -> dict[str, Any]:
    tagged = classify(article.headline, article.summary)
    record = article.record
    observed_by_cutoff = article.first_observed <= cutoff
    # The text shown is a revision observed after the cutoff, or one Alpaca edited later.
    revised_later = article.updated_at > cutoff or (record.revisions > 1 and not observed_by_cutoff)
    return {
        "article_id": article.article_id,
        "headline": article.headline,
        "summary": article.summary,
        "author": article.author,
        "source": article.source,
        "symbols": list(article.symbols),
        "url": article.url,
        "published_at_utc": utc_text(article.published_at),
        "updated_at_utc": utc_text(article.updated_at),
        "first_observed_utc": record.first_observed_utc,
        "revisions": record.revisions,
        "observed_by_cutoff": observed_by_cutoff,
        "revised_after_cutoff": revised_later,
        "categories": list(tagged.categories),
        "matched_terms": {k: list(v) for k, v in tagged.matches.items()},
        "record_ref": record.record_id,
    }


def news_check(
    news: AlpacaNews,
    instrument_id: str,
    cutoff: datetime,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
) -> dict[str, Any]:
    """The news check for one instrument at one cutoff, as `screening_check` parts.

    Statuses: `material`, `no_material`, `no_data` (no article in the window),
    `unsupported` (not a US listing) and `error` (the source failed; `result.reason`)."""
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must carry a time zone")
    if not 1 <= lookback_hours <= MAX_LOOKBACK_HOURS:
        raise ValueError(f"lookback_hours must be between 1 and {MAX_LOOKBACK_HOURS}")
    if cutoff > news.now():
        raise ValueError("the cutoff is in the future")
    start = cutoff - timedelta(hours=lookback_hours)
    document = _base(start, lookback_hours)
    result = document["result"]
    parsed = parse_instrument(instrument_id)
    if parsed is None or parsed.market != "US":
        document["status"] = "unsupported"
        result["reason"] = "unsupported_instrument"
        result["missing"].append("Alpaca News covers US listings only.")
        return document
    try:
        found = news.articles(parsed.symbol, start, cutoff)
    except SourceConfigError as exc:
        result["reason"] = exc.code
        result["missing"].append(str(exc))
        return document
    except SourceError as exc:
        result["reason"] = "source_unavailable"
        result["missing"].append(str(exc))
        return document
    document["warnings"].extend(news.notes)
    if news.truncated:
        result["missing"].append("Older articles in the window were not read (page limit).")

    known = [a for a in found if a.published_at <= cutoff]
    articles = [_article_document(a, cutoff) for a in reversed(known)]  # newest first
    result["articles"] = articles
    document["sources"]["records"] = [a.record.contract() for a in reversed(known)]

    by_category: dict[str, list[dict[str, Any]]] = {}
    for article in articles:
        for category in article["categories"]:
            by_category.setdefault(category, []).append(article)
    order = [category for category, _, _ in RULES] + [NONE]
    result["categories"] = [
        {
            "category": category,
            "label": LABELS[category],
            "article_count": len(by_category[category]),
            "latest_published_at_utc": by_category[category][0]["published_at_utc"],
        }
        for category in order
        if category in by_category
    ]

    material = [a for a in articles if a["categories"] != [NONE]]
    if not articles:
        document["status"] = "no_data"
    elif material:
        document["status"] = "material"
    else:
        document["status"] = "no_material"

    facts = [
        Fact(
            "news_count",
            str(len(articles)),
            "count",
            f"{len(articles)} articles created in the {lookback_hours} hours before the cutoff",
        ),
        Fact(
            "material_news_count",
            str(len(material)),
            "count",
            f"{len(material)} of them matched a catalyst rule",
        ),
    ]
    for entry in result["categories"]:
        if entry["category"] == NONE:
            continue
        facts.append(
            Fact(
                "catalyst_count",
                str(entry["article_count"]),
                "count",
                f"{entry['article_count']} articles tagged {entry['category']}",
                subject=entry["category"],
            )
        )
    if material:
        latest = material[0]
        age = cutoff - datetime.fromisoformat(latest["published_at_utc"].replace("Z", "+00:00"))
        facts.append(
            Fact(
                "latest_catalyst_age_hours",
                _hours(age),
                "hours",
                f"The latest tagged article ({', '.join(latest['categories'])}) was created "
                f"{_hours(age)} hours before the cutoff",
                subject=latest["article_id"],
            )
        )
    result["facts"] = [fact.document() for fact in facts]

    if any(not a["observed_by_cutoff"] for a in articles):
        result["uncertainty"].append(
            "Some articles were first downloaded after the cutoff: their availability at the "
            "cutoff is reconstructed from Alpaca's own times, not observed by SIW."
        )
    if any(a["revised_after_cutoff"] for a in articles):
        result["uncertainty"].append(
            "Some articles were edited after the cutoff; their headline or summary may show "
            "the later text."
        )
    return document
