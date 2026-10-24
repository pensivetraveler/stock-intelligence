"""A fake Kiwoom paper server for `httpx.MockTransport`: tokens, US orders, today's list.

Its replies follow the shapes in the official spec (zero-padded numbers, `return_code`,
Seoul `HH:MM:SS` times). They are fixtures, not observed paper-server replies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import httpx


@dataclass
class FakeOrder:
    ord_no: str
    stk_cd: str
    side: str  # "1" sell, "2" buy
    trde_tp: str
    qty: Decimal
    price: str
    ord_time: str = "23:31:06"
    filled: Decimal = Decimal(0)
    fill_price: Decimal = Decimal(0)
    cntr_time: str = ""
    cancelled: bool = False
    original: str = "000000000"


@dataclass
class FakeKiwoomPaper:
    requests: list[httpx.Request] = field(default_factory=list)
    orders: list[FakeOrder] = field(default_factory=list)
    tokens_issued: int = 0
    # Behaviour switches for one test at a time.
    reject_orders: bool = False
    drop_next_order: bool = False  # take the order, then lose the connection
    invalidate_next_token: bool = False
    pages: int = 1
    rate_limit_next_query: bool = False

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def fill(self, ord_no: str, price: str, at: str = "23:31:30") -> None:
        order = next(o for o in self.orders if o.ord_no == ord_no)
        order.filled, order.fill_price, order.cntr_time = order.qty, Decimal(price), at

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        api_id = request.headers.get("api-id")
        body = json.loads(request.content or b"{}")
        if api_id == "au10001":
            self.tokens_issued += 1
            return httpx.Response(
                200,
                json={
                    "token": f"token-{self.tokens_issued}",
                    "token_type": "bearer",
                    "expires_dt": "20991231235959",
                    "return_code": 0,
                    "return_msg": "정상적으로 처리되었습니다",
                },
            )
        if self.invalidate_next_token:
            self.invalidate_next_token = False
            return httpx.Response(
                200, json={"return_code": 3, "return_msg": "[8005:Token이 유효하지 않습니다]"}
            )
        if api_id in ("ust20000", "ust20001"):
            if body["trde_tp"] == "03":  # as the paper server answered on 2026-10-23
                return httpx.Response(
                    200,
                    json={
                        "return_code": 20,
                        "return_msg": "[2000](RC4065:모의투자 지정가 매매만 가능합니다.)",
                    },
                )
            if self.reject_orders:
                return httpx.Response(200, json={"return_code": 20, "return_msg": "주문 불가"})
            order = FakeOrder(
                ord_no=f"{len(self.orders) + 1:09d}",
                stk_cd=body["stk_cd"],
                side="2" if api_id == "ust20000" else "1",
                trde_tp=body["trde_tp"],
                qty=Decimal(body["ord_qty"]),
                price=body["ord_uv"],
            )
            self.orders.append(order)
            if self.drop_next_order:
                self.drop_next_order = False
                raise httpx.ReadTimeout("connection lost after sending", request=request)
            return httpx.Response(
                200,
                json={"ord_no": order.ord_no, "return_code": 0, "return_msg": "주문입력 완료"},
            )
        if api_id == "ust20003":
            order = next(o for o in self.orders if o.ord_no == body["orig_ord_no"])
            order.cancelled = True
            return httpx.Response(200, json={"ord_no": "000000999", "return_code": 0})
        if api_id == "ust21510":
            if self.rate_limit_next_query:
                self.rate_limit_next_query = False
                return httpx.Response(
                    429,
                    json={
                        "return_code": 5,
                        "return_msg": "요청 개수 초과[1700:유량=1, API ID=ust21510]",
                    },
                )
            return self._today(request)
        return httpx.Response(200, json={"return_code": 20, "return_msg": "unexpected request"})

    def _row(self, order: FakeOrder) -> dict[str, Any]:
        remaining = Decimal(0) if order.cancelled else order.qty - order.filled
        return {
            "ord_no": order.ord_no,
            "orig_ord_no": order.original,
            "stk_cd": order.stk_cd,
            "frgn_trde_tp": order.trde_tp,
            "slby_tp": order.side,
            "ord_qty": f"{int(order.qty):012d}",
            "ord_uv": f"{Decimal(order.price or '0'):.4f}",
            "cntr_qty": f"{int(order.filled):012d}",
            "cntr_uv": f"{order.fill_price:.4f}",
            "ord_remnq": f"{int(remaining):012d}",
            "ord_time": order.ord_time,
            "ord_stat": "체결" if order.filled else "접수",
            "cntr_time": order.cntr_time,
        }

    def _today(self, request: httpx.Request) -> httpx.Response:
        rows = [self._row(order) for order in self.orders]
        if self.pages > 1 and request.headers.get("cont-yn") != "Y":
            half = len(rows) // 2
            return httpx.Response(
                200,
                headers={"cont-yn": "Y", "next-key": "page-2"},
                json={"result_list": rows[:half], "return_code": 0, "return_msg": "ok"},
            )
        if self.pages > 1:
            rows = rows[len(rows) // 2 :]
        # The spec's example spells the list `result_lsit`; the fake uses that spelling.
        return httpx.Response(200, json={"result_lsit": rows, "return_code": 0, "return_msg": "ok"})
