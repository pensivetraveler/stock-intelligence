"""Screening check B — news catalysts (ADR 0052 §3), keyword rules only.

`news_check` returns the parts of one `screening_check` document (status, result,
versions, warnings, sources) for one instrument and cutoff; the route adds the envelope.
It reads every configured news feed (Alpaca News, MarketNewsAPI, Finnhub) for the
look-back window up to the cutoff, tags each article with `news_rules`, and lists source
records, versions and what could not be confirmed.

- Articles created after the cutoff are never read into the result.
- Feeds are not merged: an article keeps its feed (`source_id`). The same headline seen
  in two feeds is pointed out (`same_headline_as`), not collapsed.
- A feed without a key is skipped and said so; the check fails only when no feed could
  be read.

The check places no order and changes no entry condition; choosing an entry preset from
the catalyst (S5) is a separate, user-defined table.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from services.agents.evidence import Fact
from services.market_data.instruments import parse_instrument
from services.public_sources.alpaca_news import ALPACA_NEWS
from services.public_sources.cache import utc_text
from services.public_sources.finnhub_news import FINNHUB_NEWS
from services.public_sources.http import SourceConfigError, SourceError
from services.public_sources.marketnews import MARKETNEWS
from services.public_sources.news_common import NewsArticle, NewsFeed
from services.screening import news_rules
from services.screening.news_rules import LABELS, NONE, RULES, classify

DEFAULT_LOOKBACK_HOURS = 72
MAX_LOOKBACK_HOURS = 24 * 30

ALWAYS_UNCERTAIN = (
    "Catalyst categories come from fixed keyword rules on the headline and summary only; "
    "a missed or wrong category is possible, and 'none' means no rule matched, not that "
    "there is no catalyst.",
    "The news feeds carry some publishers and press-release wires, not all news; SEC "
    "filings are a separate check.",
)
#: What each feed adds to the uncertainty when it contributed articles.
FEED_UNCERTAIN = {
    ALPACA_NEWS: "Alpaca documents created_at only as the article's creation time; it is "
    "used as the publication time (unverified).",
    FINNHUB_NEWS: "Finnhub is queried by whole dates and documents no paging, so a busy "
    "window may be cut (unverified); it gives no update time.",
    MARKETNEWS: "MarketNewsAPI's own summaries are machine-generated and are not used for "
    "tagging; the publisher snippet is. It gives no update time.",
}


def _hours(delta: timedelta) -> str:
    return f"{delta.total_seconds() / 3600:.1f}"


def _headline_key(headline: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", headline.lower()).strip()


def _base(start: datetime, lookback_hours: int) -> dict[str, Any]:
    return {
        "status": "error",
        "result": {
            "reason": None,
            "window_start_utc": utc_text(start),
            "lookback_hours": lookback_hours,
            "feeds": [],
            "categories": [],
            "articles": [],
            "facts": [],
            "missing": [],
            "uncertainty": list(ALWAYS_UNCERTAIN),
        },
        "versions": {
            "news_rules": news_rules.RULES_VERSION,
            "news_rules_sha256": news_rules.rules_checksum(),
        },
        "warnings": [],
        "sources": {"records": []},
    }


def _article_document(article: NewsArticle, cutoff: datetime) -> dict[str, Any]:
    tagged = classify(article.headline, article.summary)
    record = article.record
    observed_by_cutoff = article.first_observed <= cutoff
    edited_later = article.updated_at is not None and article.updated_at > cutoff
    # The text shown is a revision observed after the cutoff, or one the feed edited later.
    revised_later = edited_later or (record.revisions > 1 and not observed_by_cutoff)
    return {
        "source_id": article.source_id,
        "article_id": article.article_id,
        "article_type": article.article_type,
        "headline": article.headline,
        "summary": article.summary,
        "author": article.author,
        "publisher": article.publisher,
        "symbols": list(article.symbols),
        "url": article.url,
        "published_at_utc": utc_text(article.published_at),
        "updated_at_utc": utc_text(article.updated_at) if article.updated_at else None,
        "first_observed_utc": record.first_observed_utc,
        "revisions": record.revisions,
        "observed_by_cutoff": observed_by_cutoff,
        "revised_after_cutoff": revised_later,
        "has_body": article.has_body,
        "categories": list(tagged.categories),
        "matched_terms": {k: list(v) for k, v in tagged.matches.items()},
        "record_ref": f"{record.source_id}/{record.record_id}",
        "same_headline_as": [],
    }


def news_check(
    feeds: Sequence[NewsFeed],
    instrument_id: str,
    cutoff: datetime,
    lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
) -> dict[str, Any]:
    """The news check for one instrument at one cutoff, as `screening_check` parts.

    Statuses: `material`, `no_material`, `no_data` (no article in the window),
    `unsupported` (not a US listing) and `error` (no feed could be read; `result.reason`
    is `news_credentials_missing` when no feed has a key, else `source_unavailable`)."""
    if not feeds:
        raise ValueError("at least one news feed is needed")
    if cutoff.tzinfo is None:
        raise ValueError("cutoff must carry a time zone")
    if not 1 <= lookback_hours <= MAX_LOOKBACK_HOURS:
        raise ValueError(f"lookback_hours must be between 1 and {MAX_LOOKBACK_HOURS}")
    if cutoff > feeds[0].now():
        raise ValueError("the cutoff is in the future")
    start = cutoff - timedelta(hours=lookback_hours)
    document = _base(start, lookback_hours)
    result = document["result"]
    parsed = parse_instrument(instrument_id)
    if parsed is None or parsed.market != "US":
        document["status"] = "unsupported"
        result["reason"] = "unsupported_instrument"
        result["missing"].append("The news feeds are read for US listings only.")
        return document

    found: list[NewsArticle] = []
    read_any = False
    for feed in feeds:
        state: dict[str, Any] = {
            "source_id": feed.source_id,
            "name": feed.display_name,
            "state": "ok",
            "reason": None,
            "articles": 0,
            "truncated": False,
        }
        result["feeds"].append(state)
        try:
            fetched = feed.articles(parsed.symbol, start, cutoff)
        except SourceConfigError as exc:
            state["state"], state["reason"] = "no_key", exc.code
            continue
        except SourceError as exc:
            state["state"], state["reason"] = "error", "source_unavailable"
            result["missing"].append(f"{feed.display_name}: {exc}")
            document["warnings"].append(f"{feed.display_name} could not be read: {exc}")
            continue
        read_any = True
        known = [a for a in fetched if a.published_at <= cutoff]
        state["articles"] = len(known)
        state["truncated"] = feed.truncated
        document["versions"][f"parser.{feed.source_id}"] = feed.parser_version
        document["warnings"].extend(feed.notes)
        if feed.truncated:
            result["missing"].append(
                f"{feed.display_name}: older articles in the window were not read (page limit)."
            )
        if known and feed.source_id in FEED_UNCERTAIN:
            result["uncertainty"].append(FEED_UNCERTAIN[feed.source_id])
        found.extend(known)

    if not read_any:
        states = {s["state"] for s in result["feeds"]}
        no_keys = states == {"no_key"}
        result["reason"] = "news_credentials_missing" if no_keys else "source_unavailable"
        if no_keys:
            result["missing"].append(
                "No news feed has a key: register Alpaca, MarketNewsAPI or Finnhub on the "
                "Keys screen."
            )
        return document

    found.sort(key=lambda a: (a.published_at, a.source_id, a.article_id), reverse=True)
    articles = [_article_document(a, cutoff) for a in found]  # newest first
    by_headline: dict[str, list[dict[str, Any]]] = {}
    for article in articles:
        by_headline.setdefault(_headline_key(article["headline"]), []).append(article)
    for group in by_headline.values():
        if len({a["source_id"] for a in group}) > 1:
            for article in group:
                article["same_headline_as"] = [
                    other["record_ref"]
                    for other in group
                    if other is not article and other["source_id"] != article["source_id"]
                ]
    result["articles"] = articles
    document["sources"]["records"] = [a.record.contract() for a in found]

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
    for state in result["feeds"]:
        if state["state"] == "ok":
            facts.append(
                Fact(
                    "feed_article_count",
                    str(state["articles"]),
                    "count",
                    f"{state['articles']} of them from {state['name']}",
                    subject=state["source_id"],
                )
            )
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
                subject=latest["record_ref"],
            )
        )
    result["facts"] = [fact.document() for fact in facts]

    if any(not a["observed_by_cutoff"] for a in articles):
        result["uncertainty"].append(
            "Some articles were first downloaded after the cutoff: their availability at the "
            "cutoff is reconstructed from the feeds' own times, not observed by SIW."
        )
    if any(a["revised_after_cutoff"] for a in articles):
        result["uncertainty"].append(
            "Some articles were edited after the cutoff; their headline or summary may show "
            "the later text."
        )
    return document
