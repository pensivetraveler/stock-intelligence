"""The paper runner with a wait for the user before every order (ADR 0047 §1).

An intent that passed its limit and kill-switch checks is not sent: it waits for the
user's confirmation on the SIW Agent screen. A confirmed intent is checked again and
sent on the next step; one not confirmed in time expires, and one declined is closed.
While one waits, its instrument takes no new signal or protective exit.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from services.paper_trading.runner import PaperRunner, StepReport

# A confirmed intent is sent on the next step; past this much after its deadline it is
# not sent any more (the runner may have been stopped in between).
SEND_GRACE_SECONDS = 30
PROTECTIVE_SUMMARY = "protective exit:"


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


class LiveRunner(PaperRunner):
    fill_origin = "broker_live"

    def _confirm_seconds(self) -> int:
        if self.plan.confirm_seconds is None:
            raise ValueError("a live plan needs confirm_seconds")
        return self.plan.confirm_seconds

    def _dispatch(
        self, intent: dict[str, Any], now: datetime, report: StepReport, exit: bool
    ) -> None:
        expires = now + timedelta(seconds=self._confirm_seconds())
        self.store.request_confirmation(intent["intent_id"], expires)
        report.awaiting += 1

    def _awaiting(self, instrument_id: str) -> bool:
        return any(
            item["intent"]["instrument_id"] == instrument_id
            for item in self.store.open_confirmations()
        )

    def _settle_confirmations(self, now: datetime, report: StepReport) -> None:
        for item in self.store.open_confirmations():
            intent = item["intent"]
            identifier = intent["intent_id"]
            if intent["approval_id"] != self.approval_id:
                self.store.set_outcome(identifier, "blocked", "approval_changed")
                report.blocked += 1
                continue
            expires = _time(item["expires_at_utc"])
            if item["decision"] == "declined":
                self.store.set_outcome(identifier, "blocked", "declined_by_user")
                report.blocked += 1
                continue
            if item["decision"] is None:
                if now >= expires:
                    self.store.set_outcome(identifier, "expired", "not_confirmed")
                    report.expired += 1
                continue
            if (now - expires).total_seconds() > SEND_GRACE_SECONDS:
                self.store.set_outcome(identifier, "expired", "confirmed_but_not_sent_in_time")
                report.expired += 1
                continue
            protective = str(intent["signal"]["summary"]).startswith(PROTECTIVE_SUMMARY)
            allowed, reason = (
                self.store.may_exit(self.approval_id)
                if protective
                else self.store.may_submit(self.approval_id)
            )
            if allowed and self.store.unknown_submission_count() > 0:
                allowed, reason = False, "unknown_submission_pending"
            if not allowed:
                self.store.set_outcome(identifier, "blocked", reason)
                report.blocked += 1
                continue
            self._submit(intent, now)
            report.submitted += 1
