"""Check B (ADR 0052 §3): news catalysts from the news-analysis service.

Collecting news, analyzing one article and analyzing a ticker's news cycle are separate
modules in their own project, the news-analysis service (`~/Trading/news-analysis`,
contract `news-contract-v1`), at 127.0.0.1:8776 (2026-11-09 user decision). SIW asks it
for a news check at the screening cutoff, waits a little, and keeps the service's
`news_check` JSON unchanged next to a short summary for the card.

- SIW never starts the service: when it is down the check waits (`pending`).
- The service keeps its own keys and its own article store; SIW stores no article.
- Dilution news (offerings, warrants, converts) is only flagged by the service
  (`out_of_scope.dilution`); dilution is judged by check C.
- The service may add an LLM reading of the tagged articles (`nir`, news-analysis ADR
  0002). It is inference, never verified fact, and never changes `status`. The card shows
  a short digest marked as inference; without one it says the result is rules only.
  News is optional: no articles, no LLM or a stopped service never drops a candidate.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

CHECK_VERSION = "screening-news-4"
CONTRACT = "news-contract-v1"
SERVICE_NAME = "news-analysis"
DEFAULT_URL = "http://127.0.0.1:8776"
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
#: The service's statuses that are also screening statuses.
STATUSES = frozenset({"material", "no_material", "no_data", "error"})
#: NIR digest fields copied for the card (all inference).
NIR_FIELDS = (
    "event_type",
    "counterparty",
    "legally_binding",
    "purchase_obligation",
    "committed_amount",
    "potential_amount",
    "economic_substance",
    "promotion_risk",
    "evidence_strength",
    "confidence",
    "summary_ko",
    "verification_failures",
    "rule_conflicts",
)
MAX_NIR_ITEMS = 5


class NewsServiceDown(RuntimeError):
    """The news-analysis service did not answer (not running, or refused the connection)."""


class NewsServiceError(RuntimeError):
    """The service answered with something SIW cannot use."""


def service_url() -> str:
    url = os.getenv("SIW_NEWS_URL", DEFAULT_URL).strip().rstrip("/")
    if urlsplit(url).hostname not in LOOPBACK:
        raise NewsServiceError("SIW_NEWS_URL must point to this computer")
    return url


@dataclass
class NewsService:
    """The service's local API: news checks, their jobs and results."""

    base_url: str
    transport: httpx.BaseTransport | None = None

    def _client(self) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(10.0, connect=2.0),
            transport=self.transport,
            follow_redirects=False,
        )

    def _call(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            with self._client() as client:
                return client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise NewsServiceDown(f"the news-analysis service did not answer: {exc}") from exc

    def submit(
        self,
        ticker: str,
        cutoff: datetime,
        lookback_hours: int,
        context: dict[str, Any] | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "ticker": ticker,
            "cutoff_utc": cutoff.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            "lookback_hours": lookback_hours,
            "refresh": "auto",
        }
        if context:
            # news_check 1.1: workstation inputs for the LLM step, each known by the cutoff.
            body["context"] = context
        response = self._call("POST", "/api/v1/news-checks", json=body)
        if response.status_code != 202:
            raise NewsServiceError(
                f"the news service refused the request ({response.status_code}): "
                f"{response.text[:200]}"
            )
        return str(response.json()["job_id"])

    def job(self, job_id: str) -> dict[str, Any]:
        response = self._call("GET", f"/api/v1/jobs/{job_id}")
        if response.status_code != 200:
            raise NewsServiceError(f"no such news job ({response.status_code})")
        return dict(response.json())

    def result(self, result_id: str) -> dict[str, Any]:
        response = self._call("GET", f"/api/v1/results/{result_id}")
        if response.status_code != 200:
            raise NewsServiceError(f"no such news result ({response.status_code})")
        document = dict(json.loads(response.content))
        if document.get("kind") != "news_check" or document.get("contract") != CONTRACT:
            raise NewsServiceError(
                f"the news service answered a {document.get('kind')!r} "
                f"of contract {document.get('contract')!r}, not {CONTRACT}"
            )
        return document


def nir_digest(check: dict[str, Any]) -> dict[str, Any]:
    """The card's view of the service's LLM step: inference only, or rules only.

    A result made before the step existed, or by a service without it, has no `nir`
    block; that is shown as rules only (`state: null`).
    """
    block = check.get("nir")
    if not isinstance(block, dict):
        return {
            "label": "INFERENCE",
            "state": None,
            "reason": None,
            "rules_only": True,
            "model": None,
            "items": [],
        }
    headlines = {(a.get("source_id"), a.get("record_id")): a for a in check.get("articles") or ()}
    items = []
    for item in block.get("items") or ():
        digest = item.get("summary")
        if item.get("state") not in ("created", "reused") or not isinstance(digest, dict):
            continue
        ref = item.get("article") or {}
        article = headlines.get((ref.get("source_id"), ref.get("record_id"))) or {}
        items.append(
            {
                "nir_record_id": item.get("nir_record_id"),
                "source_id": ref.get("source_id"),
                "record_id": ref.get("record_id"),
                "headline": article.get("headline"),
                "published_at_utc": article.get("published_at_utc"),
                **{name: digest.get(name) for name in NIR_FIELDS},
            }
        )
        if len(items) >= MAX_NIR_ITEMS:
            break
    return {
        "label": "INFERENCE",
        "state": block.get("state"),
        "reason": block.get("reason"),
        "rules_only": not items,
        "model": block.get("model"),
        "items": items,
    }


def summary(check: dict[str, Any]) -> dict[str, Any]:
    """What the card shows: categories, dilution flags, feeds, the article count and the
    service's LLM digest (inference)."""
    feeds = [
        {
            "source_id": feed.get("source_id"),
            "state": feed.get("state"),
            "articles": feed.get("articles"),
            "truncated": bool(feed.get("truncated")),
        }
        for feed in check.get("feeds") or ()
    ]
    articles = check.get("articles") or []
    classifications = check.get("classifications") or []
    latest = None
    for article, tagged in zip(reversed(articles), reversed(classifications), strict=False):
        if tagged.get("categories"):
            latest = {
                "source_id": article.get("source_id"),
                "record_id": article.get("record_id"),
                "headline": article.get("headline"),
                "published_at_utc": article.get("published_at_utc"),
                "categories": list(tagged.get("categories") or ()),
            }
            break
    return {
        "reason": check.get("reason"),
        "window_start_utc": check.get("window_start_utc"),
        "lookback_hours": check.get("lookback_hours"),
        "article_count": len(articles),
        "categories": dict(check.get("categories") or {}),
        "out_of_scope": dict(check.get("out_of_scope") or {}),
        "feeds": feeds,
        "latest_categorized": latest,
        "nir": nir_digest(check),
    }


def follow(
    service: NewsService,
    job_id: str,
    wait_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = 0.5,
) -> tuple[str, dict[str, Any]]:
    """Waits up to `wait_seconds` for a job and returns (status, result) for the check."""
    waited = 0.0
    while True:
        envelope = service.job(job_id)
        state = str(envelope.get("state"))
        if state == "done":
            break
        if state == "failed":
            error = envelope.get("error") or {}
            return "error", {
                "job_id": job_id,
                "service_state": state,
                "reason": f"the news check failed: {error.get('code')}: "
                f"{str(error.get('message') or '')[:300]}",
            }
        if waited >= wait_seconds:
            return "pending", {"job_id": job_id, "service_state": state}
        sleep(poll_seconds)
        waited += poll_seconds
    result_id = str(envelope["result_id"])
    check = service.result(result_id)
    status = str(check.get("status"))
    if status not in STATUSES:
        raise NewsServiceError(f"the news service answered an unknown status {status!r}")
    return status, {
        "job_id": job_id,
        "result_id": result_id,
        "service_state": "done",
        **summary(check),
        "news_check": check,
    }
