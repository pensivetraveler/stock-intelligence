"""Check C (ADR 0052): dilution supply price levels from the dilution analyzer service.

The analyzer ("주식 희석 분석기", its own repository and session) runs on this machine at
127.0.0.1:8775. SIW asks it for an analysis, waits a little, and keeps the result JSON
unchanged next to a summary of the contract prices on it: exercise and conversion prices,
price ranges, floors, condition thresholds, ATM capacity, shelves and resale shares.
Nothing here forecasts a price. Values the analyzer marks other than confirmed, computed
or inferred are kept with their status, and a missing value stays null, never 0.

Agreed with the analyzer session ([회신 #SIW-screen-1]) and confirmed by the user: every
candidate is analyzed rules-only (`llm=false`); an LLM analysis only for a few the user
picks. SIW never starts the service: when it is down the check waits.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.parse import urlsplit

import httpx

CHECK_VERSION = "screening-dilution-1"
DEFAULT_URL = "http://127.0.0.1:8775"
LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
FACT_STATUSES = frozenset({"confirmed", "computed", "inferred"})
NO_RESULT_STATES = frozenset({"blocked", "unsupported", "failed"})
AFTER_BASE_MARK = "(기준 시점 뒤 발행)"


class DilutionServiceDown(RuntimeError):
    """The analyzer service did not answer (not running, or refused the connection)."""


class DilutionServiceError(RuntimeError):
    """The analyzer answered with something SIW cannot use."""


def service_url() -> str:
    url = os.getenv("SIW_DILUTION_URL", DEFAULT_URL).strip().rstrip("/")
    if urlsplit(url).hostname not in LOOPBACK:
        raise DilutionServiceError("SIW_DILUTION_URL must point to this computer")
    return url


@dataclass
class DilutionService:
    """The analyzer's local API. Only its analysis and result routes are called."""

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
            raise DilutionServiceDown(f"the dilution analyzer did not answer: {exc}") from exc

    def submit(self, ticker: str, as_of: date, llm: bool) -> str:
        response = self._call(
            "POST",
            "/api/v1/analyses",
            json={"ticker": ticker, "as_of": as_of.isoformat(), "llm": llm},
        )
        if response.status_code != 202:
            raise DilutionServiceError(
                f"the analyzer refused the request ({response.status_code}): {response.text[:200]}"
            )
        return str(response.json()["job_id"])

    def job(self, job_id: str) -> dict[str, Any]:
        response = self._call("GET", f"/api/v1/analyses/{job_id}")
        if response.status_code != 200:
            raise DilutionServiceError(f"no such analysis job ({response.status_code})")
        return dict(response.json())

    def result(self, result_id: str) -> dict[str, Any]:
        response = self._call("GET", f"/api/v1/results/{result_id}")
        if response.status_code == 422:
            return {"blocked": True, **response.json()}
        if response.status_code != 200:
            raise DilutionServiceError(f"no such result ({response.status_code})")
        return dict(json.loads(response.content))


def _value(item: Any) -> dict[str, Any] | None:
    """A contract value reduced to its amount, unit and status (amount null = unknown)."""
    if not isinstance(item, dict):
        return None
    return {
        "amount": item.get("amount"),
        "unit": item.get("unit"),
        "status": item.get("status"),
        "fact": item.get("status") in FACT_STATUSES and item.get("amount") is not None,
    }


def supply_levels(contract: dict[str, Any]) -> dict[str, Any]:
    """The price-axis summary of one analyzer result (contract v1)."""
    securities = []
    for security in contract.get("securities") or []:
        gates = security.get("gates") or []
        pricing = security.get("pricing") or {}
        price_range = security.get("price_range")
        securities.append(
            {
                "id": security.get("id"),
                "kind": security.get("kind"),
                "description": security.get("description"),
                "price": _value(security.get("price")),
                "price_range": [_value(item) for item in price_range] if price_range else None,
                "floor": _value(pricing.get("floor")) if pricing else None,
                "outstanding": _value(security.get("outstanding")),
                "conditions": [
                    {"type": item.get("type"), "threshold": _value(item.get("threshold"))}
                    for item in security.get("conditions") or []
                ],
                "after_base_point": AFTER_BASE_MARK in str(security.get("description") or ""),
                "approval_unknown": any(
                    gate.get("kind") == "shareholder_approval" and gate.get("state") == "unknown"
                    for gate in gates
                ),
            }
        )
    dilution = (contract.get("dilution") or {}).get("as_of") or {}
    shelves = contract.get("shelves") or {}
    resale = contract.get("resale") or {}
    issuer = contract.get("issuer") or {}
    meta = contract.get("meta") or {}
    return {
        "securities": securities,
        "capacity": [
            {"security_id": item.get("security_id"), "nominal": _value(item.get("nominal"))}
            for item in contract.get("capacity") or []
        ],
        "shelves": [
            {
                "file_number": item.get("file_number"),
                "status": item.get("status"),
                "registered_amount": (item.get("registered_amount") or {}).get("amount"),
                "baby_shelf": item.get("baby_shelf") is not None,
            }
            for item in shelves.get("items") or []
        ],
        "shelves_notes": shelves.get("notes") or [],
        "resale": [
            {"shares": item.get("shares"), "type": item.get("type")}
            for item in resale.get("items") or []
        ],
        "resale_notes": resale.get("notes") or [],
        "incomplete_reasons": [
            item.get("code") for item in dilution.get("incomplete_reasons") or []
        ],
        "issuer_warnings": issuer.get("warnings") or [],
        "meta": {
            key: meta.get(key)
            for key in (
                "as_of",
                "generated_at",
                "calculation_version",
                "prompt_version",
                "extract_model",
                "input_hash",
            )
        },
        "contract_schema_version": contract.get("schema_version"),
    }


def follow(
    service: DilutionService,
    job_id: str,
    wait_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = 1.0,
) -> tuple[str, dict[str, Any]]:
    """Waits up to `wait_seconds` for a job and returns (status, result) for the check."""
    waited = 0.0
    while True:
        envelope = service.job(job_id)
        state = str(envelope.get("state"))
        if state == "done":
            break
        if state in NO_RESULT_STATES:
            reason = {
                "blocked": "the analyzer blocked the export",
                "unsupported": "the analyzer does not support this ticker",
                "failed": "the analysis failed",
            }[state]
            status = "unsupported" if state == "unsupported" else "error"
            return status, {
                "job_id": job_id,
                "service_state": state,
                "reason": f"{reason}: {str(envelope.get('error') or '')[:300]}",
                "violations": envelope.get("violations"),
            }
        if waited >= wait_seconds:
            return "pending", {"job_id": job_id, "service_state": state}
        sleep(poll_seconds)
        waited += poll_seconds
    result_id = str(envelope["result_id"])
    contract = service.result(result_id)
    if contract.get("blocked"):
        return "error", {
            "job_id": job_id,
            "result_id": result_id,
            "service_state": "blocked",
            "reason": "the analyzer blocked the export",
            "violations": contract.get("violations"),
        }
    levels = supply_levels(contract)
    return ("incomplete" if levels["incomplete_reasons"] else "ok"), {
        "job_id": job_id,
        "result_id": result_id,
        "service_state": "done",
        "reused": bool(envelope.get("reused")),
        "incomplete": envelope.get("incomplete"),
        **levels,
        "contract": contract,
    }
