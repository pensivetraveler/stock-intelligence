"""Kiwoom US paper orders (모의투자) through `mockapi.kiwoom.com` only (ADR 0037).

From the official REST spec (kiwoom-spec index): orders `ust20000` (buy), `ust20001`
(sell), `ust20003` (cancel) on POST /api/us/ordr; today's orders and fills `ust21510`
on POST /api/us/acnt. Order bodies carry no account
number: the token decides the account.

Checked on the paper server (2026-10-23, read-only): a token is issued and `ust21070`
answers; `ust31490` (orderable quantity) is "not offered in paper trading" (RC9000), so it
is not on the allowlist. Whether the paper server takes US orders and answers `ust21510`
is unverified; until the first approved paper order (P4) the adapter offers no market,
so the runner's limit check blocks every order.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx

from services.market_data.broker_common import TokenCache
from services.paper_trading.brokers.base import (
    BrokerOrder,
    Capabilities,
    OrderNotSupportedError,
    SubmitResult,
)
from services.paper_trading.paper_client import PaperOnlyClient, require_paper_environment

SEOUL = ZoneInfo("Asia/Seoul")
TOKEN = ("POST", "/oauth2/token", "au10001")
BUY = ("POST", "/api/us/ordr", "ust20000")
SELL = ("POST", "/api/us/ordr", "ust20001")
CANCEL = ("POST", "/api/us/ordr", "ust20003")
TODAY_ORDERS = ("POST", "/api/us/acnt", "ust21510")
ALLOWED = (TOKEN, BUY, SELL, CANCEL, TODAY_ORDERS)
# Kiwoom `stex_tp`: NA AMEX, ND NASDAQ, NY NYSE. NYSE Arca (ARCX) and BATS have no code.
EXCHANGE_CODES = {"xnas": "ND", "xnys": "NY", "xase": "NA"}
ORDER_TYPE_CODES = {"limit": "00", "market": "03"}
TOKEN_INVALID_CODE = 3
NOT_OFFERED_IN_PAPER = "RC9000"
MAX_PAGES = 50


class KiwoomPaperError(RuntimeError):
    pass


def _decimal(value: object) -> Decimal:
    text = str(value or "0").strip() or "0"
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise KiwoomPaperError(f"Kiwoom returned a number that is not a number: {text!r}") from exc


def _price_text(price: Decimal) -> str:
    return format(price.normalize(), "f")


class KiwoomPaperBroker:
    broker_id: Literal["kiwoom"] = "kiwoom"
    time_zone = "Asia/Seoul"

    def __init__(
        self,
        app_key: str,
        secret_key: str,
        environment: str | None,
        *,
        markets: frozenset[str] = frozenset(),
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        require_paper_environment(environment)
        self._app_key = app_key
        self._secret_key = secret_key
        self._markets = markets
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._http = PaperOnlyClient("kiwoom", ALLOWED, transport=transport)
        self._token = TokenCache(self._issue_token)
        self.account_ref = f"kiwoom-{hashlib.sha256(app_key.encode()).hexdigest()[:16]}-demo"

    def __repr__(self) -> str:
        return f"KiwoomPaperBroker(account_ref={self.account_ref!r})"

    def capabilities(self) -> Capabilities:
        return Capabilities(markets=self._markets, order_types=frozenset(ORDER_TYPE_CODES))

    def order_type_code(self, order_type: str) -> str:
        if order_type not in ORDER_TYPE_CODES:
            raise OrderNotSupportedError(f"order type {order_type} is not offered")
        return ORDER_TYPE_CODES[order_type]

    # -- transport -------------------------------------------------------------------

    def _issue_token(self) -> tuple[str, float]:
        response = self._http.request(
            TOKEN[0],
            TOKEN[1],
            headers={"api-id": TOKEN[2], "content-type": "application/json;charset=UTF-8"},
            json={
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "secretkey": self._secret_key,
            },
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise KiwoomPaperError(
                f"Kiwoom token reply is not JSON ({response.status_code})"
            ) from exc
        token = payload.get("token")
        if response.status_code >= 400 or not token or int(payload.get("return_code", 1) or 0):
            raise KiwoomPaperError(
                f"Kiwoom refused to issue a paper token ({response.status_code})"
            )
        lifetime = 3_600.0
        try:
            expires = datetime.strptime(str(payload.get("expires_dt", "")), "%Y%m%d%H%M%S")
            lifetime = max(60.0, (expires.replace(tzinfo=SEOUL) - self._now()).total_seconds())
        except ValueError:
            pass
        return str(token), lifetime

    def _post(
        self,
        endpoint: tuple[str, str, str],
        body: dict[str, str],
        extra: dict[str, str] | None = None,
    ) -> tuple[httpx.Response, dict[str, Any]]:
        """One request; a token replaced by another client (3 / 8005) is reissued once."""
        for attempt in range(2):
            headers = {
                "api-id": endpoint[2],
                "authorization": f"Bearer {self._token.get(refresh=attempt > 0)}",
                "content-type": "application/json;charset=UTF-8",
                **(extra or {}),
            }
            response = self._http.request(endpoint[0], endpoint[1], json=body, headers=headers)
            try:
                payload = dict(response.json())
            except ValueError:
                payload = {}
            invalid_token = int(payload.get("return_code", 0) or 0) == TOKEN_INVALID_CODE and (
                "8005" in str(payload.get("return_msg", ""))
            )
            if (response.status_code == 401 or invalid_token) and attempt == 0:
                continue  # rejected before it was processed: safe to send again
            return response, payload
        return response, payload

    # -- orders ----------------------------------------------------------------------

    def _venue(self, instrument_id: str) -> str:
        parts = instrument_id.split(":")
        if len(parts) != 4 or parts[0] != "us" or parts[2] not in EXCHANGE_CODES:
            raise OrderNotSupportedError(f"Kiwoom has no US exchange code for {instrument_id}")
        return EXCHANGE_CODES[parts[2]]

    def submit(self, intent: dict[str, Any]) -> SubmitResult:
        order = intent["order"]
        order_type = order["order_type"]
        if order_type not in ORDER_TYPE_CODES:
            raise OrderNotSupportedError(f"order type {order_type} is not offered")
        endpoint = BUY if intent["side"] == "buy" else SELL
        code = ORDER_TYPE_CODES[order_type]
        body = {
            "stex_tp": self._venue(intent["instrument_id"]),
            "stk_cd": intent["symbol"],
            "ord_qty": _price_text(Decimal(order["quantity"])),
            "ord_uv": "" if order_type == "market" else _price_text(Decimal(order["limit_price"])),
            "trde_tp": code,
        }
        return self._send(endpoint, body, code, reference_field="ord_no")

    def cancel(self, broker_order_ref: str, instrument_id: str, symbol: str) -> SubmitResult:
        body = {
            "orig_ord_no": broker_order_ref,
            "stex_tp": self._venue(instrument_id),
            "stk_cd": symbol,
        }
        return self._send(CANCEL, body, "cancel", reference_field="ord_no")

    def _send(
        self, endpoint: tuple[str, str, str], body: dict[str, str], code: str, reference_field: str
    ) -> SubmitResult:
        try:
            response, payload = self._post(endpoint, body)
        except httpx.HTTPError:
            # Sent or not, no reply was read: the outcome is unknown and is never retried.
            return SubmitResult("unknown", endpoint[2], code, response_message="no reply read")
        if not payload:
            if response.status_code >= 500:
                return SubmitResult(
                    "unknown", endpoint[2], code, response_code=str(response.status_code)
                )
            return SubmitResult(
                "rejected", endpoint[2], code, response_code=str(response.status_code)
            )
        return_code = str(payload.get("return_code", ""))
        message = str(payload.get("return_msg", ""))[:200] or None
        if response.status_code < 400 and return_code == "0" and payload.get(reference_field):
            return SubmitResult(
                "accepted",
                endpoint[2],
                code,
                broker_order_ref=str(payload[reference_field]),
                response_code=return_code,
                response_message=message,
            )
        return SubmitResult(
            "rejected",
            endpoint[2],
            code,
            response_code=return_code or None,
            response_message=message,
        )

    # -- queries ---------------------------------------------------------------------

    def _query(self, endpoint: tuple[str, str, str], body: dict[str, str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        continuation: dict[str, str] | None = None
        for _page in range(MAX_PAGES):
            response, payload = self._post(endpoint, body, continuation)
            message = str(payload.get("return_msg", ""))
            if NOT_OFFERED_IN_PAPER in message:
                raise KiwoomPaperError(f"{endpoint[2]} is not offered in paper trading")
            if response.status_code >= 400 or str(payload.get("return_code", "")) != "0":
                raise KiwoomPaperError(
                    f"Kiwoom {endpoint[2]} failed ({response.status_code} "
                    f"{payload.get('return_code')}): {message[:120]}"
                )
            # The spec's example spells the list `result_lsit`; accept both spellings.
            page = payload.get("result_list") or payload.get("result_lsit") or []
            rows.extend(row for row in page if isinstance(row, dict))
            if response.headers.get("cont-yn") != "Y":
                return rows
            continuation = {"cont-yn": "Y", "next-key": response.headers.get("next-key", "")}
        raise KiwoomPaperError(f"Kiwoom {endpoint[2]} returned more than {MAX_PAGES} pages")

    def today_orders(self) -> list[BrokerOrder]:
        orders: list[BrokerOrder] = []
        for row in self._query(TODAY_ORDERS, {"slby_tp": "0", "stex_tp": "", "stk_cd": ""}):
            side = {"1": "sell", "2": "buy"}.get(str(row.get("slby_tp", "")))
            reference = str(row.get("ord_no", "")).strip()
            if side is None or not reference:
                continue
            filled = _decimal(row.get("cntr_qty"))
            orders.append(
                BrokerOrder(
                    broker_order_ref=reference,
                    symbol=str(row.get("stk_cd", "")).strip(),
                    side="buy" if side == "buy" else "sell",
                    order_type_code=str(row.get("frgn_trde_tp", "")),
                    order_quantity=_decimal(row.get("ord_qty")),
                    filled_quantity=filled,
                    fill_price=_decimal(row.get("cntr_uv")) if filled > 0 else None,
                    remaining_quantity=_decimal(row.get("ord_remnq")),
                    status_text=str(row.get("ord_stat", ""))[:40],
                    ordered_at_local=str(row.get("ord_time") or "") or None,
                    filled_at_local=str(row.get("cntr_time") or "") or None,
                )
            )
        return orders
