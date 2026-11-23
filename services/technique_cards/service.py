"""From a library source to a checked technique card (ADR 0056 L1 + L2).

1. `preview`: fetch the source's transcript into the cache (or reuse it after a recheck) and
   say what would be sent: slices, characters, model and a rough cost. Nothing leaves the
   machine yet.
2. `extract`: the user pressed the button. Recheck, call the model slice by slice, merge,
   run the script checks, store the card with its log (model, tokens, cost, flag counts).
3. `recheck`: before a card is used again. `stop_using` / `unknown` deletes the cached text and
   marks the card's evidence withdrawn.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from services.technique_cards.checks import check_card, timeline
from services.technique_cards.citations import (
    Line,
    format_locator,
    join_volumes,
    parse_source,
    unit_of,
)
from services.technique_cards.extract import (
    CHOOSABLE_MODELS,
    PRICES,
    VERSIONS,
    Caller,
    Extraction,
    Keys,
    chain,
    extract_card,
    ladder,
    prompts_for,
)
from services.technique_cards.library import (
    SERIES_PREFIX,
    USABLE,
    Fetched,
    Library,
    LibraryError,
    Source,
)
from services.technique_cards.store import CardStore

# Rough sizes for the preview only (not measured per source): Korean and English captions
# average about 2.5 characters a token; a structured card was ~6000 output tokens a slice.
CHARS_PER_TOKEN = Decimal("2.5")
OUTPUT_TOKENS_PER_PART = 6000
# The list costs one CLI call per item (~1 s each); it is kept this long unless refreshed.
SOURCES_TTL_SECONDS = 300.0


class SourceWithdrawnError(RuntimeError):
    """The library says the source may no longer be used."""


@dataclass
class TechniqueCards:
    library: Library
    store: CardStore
    _listed: tuple[float, list[Source]] | None = field(default=None, repr=False)

    def sources(self, refresh: bool = False) -> list[Source]:
        now = time.monotonic()
        if refresh or self._listed is None or now - self._listed[0] > SOURCES_TTL_SECONDS:
            self._listed = (now, self.library.sources())
        return self._listed[1]

    def source(self, item_ref: str) -> Source | None:
        """One source: a single item from the library, or a series from the (cached) list."""
        if not item_ref.startswith(SERIES_PREFIX):
            return self.library.source(item_ref)
        for refresh in (False, True):
            for source in self.sources(refresh):
                if source.item_ref == item_ref:
                    return source
        return None

    def _usable_source(self, source: Source) -> tuple[Fetched, str, list[dict[str, Any]]]:
        """The source's text and recheck outcome; a series also says where each volume is."""
        if not source.is_series:
            fetched, outcome = self._usable(source.item_ref)
            return fetched, outcome, []
        parts = [self._usable(volume.item_ref) for volume in source.members]
        texts = [f.path.read_text(encoding="utf-8", errors="replace") for f, _ in parts]
        try:
            text, ranges = join_volumes(texts)
        except ValueError as exc:
            raise LibraryError(str(exc)) from exc
        fetched = self.library.keep_series(source.item_ref, text, [f for f, _ in parts])
        outcome = "conditional" if any(o == "conditional" for _, o in parts) else "still_usable"
        unit = unit_of(self._lines(fetched))
        volumes = [
            {
                "item_ref": volume.item_ref,
                "title": volume.title,
                "index": volume.series_index,
                "from": format_locator(unit, first),
                "to": format_locator(unit, last),
            }
            for volume, (first, last) in zip(source.members, ranges, strict=True)
        ]
        return fetched, outcome, volumes

    @staticmethod
    def _prompt_title(source: Source, volumes: list[dict[str, Any]]) -> str:
        """A series tells the model where each volume is in the joined text."""
        if not volumes:
            return source.title
        where = ", ".join(f"{v['index']}권 {v['from']}–{v['to']}" for v in volumes)
        return f"{source.title} (시리즈 {len(volumes)}권: {where})"

    def _usable(self, item_ref: str) -> tuple[Fetched, str]:
        # Under the item's lock, so a request that arrives while another is fetching the same
        # item waits and then reuses that cache instead of fetching it again.
        with self.library.lock(item_ref):
            return self._usable_locked(item_ref)

    def _usable_locked(self, item_ref: str) -> tuple[Fetched, str]:
        cached = self.library.cached(item_ref)
        if cached is not None:
            outcome = self.library.recheck(cached.context_ref, cached.revision_ref)
            if outcome in USABLE:
                return cached, outcome
            self.library.forget(item_ref)
            raise SourceWithdrawnError(f"library recheck: {outcome}")
        fetched = self.library.fetch(item_ref)
        outcome = self.library.recheck(fetched.context_ref, fetched.revision_ref)
        if outcome not in USABLE:
            self.library.forget(item_ref)
            raise SourceWithdrawnError(f"library recheck: {outcome}")
        return fetched, outcome

    @staticmethod
    def _lines(fetched: Fetched) -> list[Line]:
        return parse_source(fetched.path.read_text(encoding="utf-8", errors="replace"))

    def preview(self, source: Source, model: str | None = None) -> dict[str, Any]:
        model = model or self.store.default_model()
        fetched, outcome, volumes = self._usable_source(source)
        lines = self._lines(fetched)
        prompts = prompts_for(
            source.source_key or source.item_ref, self._prompt_title(source, volumes), lines
        )
        input_tokens = sum(int(Decimal(len(p)) / CHARS_PER_TOKEN) for p in prompts)
        output_tokens = OUTPUT_TOKENS_PER_PART * len(prompts)
        price_in, price_out = PRICES[model]
        cost = (price_in * input_tokens + price_out * output_tokens).scaleb(-6)
        return {
            "item_ref": source.item_ref,
            "title": source.title,
            "url": source.url,
            "lines": len(lines),
            "source_unit": unit_of(lines),
            "parts": len(prompts),
            "characters": sum(len(p) for p in prompts),
            "model": model,
            "fallback": " → ".join(ladder(model)[1:]),
            # Where a slice goes when a model is at its limit (#LAG-SIW-4).
            "limit_fallback": " → ".join(chain(model, self.store.default_model())[1:]),
            "models": list(CHOOSABLE_MODELS),
            "default_model": self.store.default_model(),
            "estimated_cost_usd": format(cost.quantize(Decimal("0.01")), "f"),
            "estimate_note": "추정(글자 수 기준, 실측 아님)",
            "verification_status": fetched.verification_status,
            "data_grade": fetched.data_grade,
            "recheck_outcome": outcome,
            "volumes": volumes,
        }

    def extract(
        self,
        source: Source,
        keys: Keys,
        call: Caller | None = None,
        model: str | None = None,
        replace: str | None = None,
    ) -> str:
        """Extract, check and store; returns the card id. Raises on any failure.

        `replace` is a card of the same source the user chose to give a new version to,
        instead of a new card (ADR 0056, 2026-11-23)."""
        model = model or self.store.default_model()
        fetched, outcome, volumes = self._usable_source(source)
        lines = self._lines(fetched)
        extraction: Extraction = extract_card(
            source.source_key or source.item_ref,
            self._prompt_title(source, volumes),
            lines,
            keys,
            call=call,
            model=model,
            default=self.store.default_model(),
            limits=self.store,
        )
        checks = check_card(extraction.card, lines)
        usage = extraction.usage | {"parts": extraction.parts, "flags": checks["counts"]}
        fields = {
            "item_ref": source.item_ref,
            "title": source.title,
            "url": source.url,
            "card": extraction.card,
            "checks": checks,
            "usage": usage,
            "attempts": extraction.attempts,
            "versions": VERSIONS
            | {"model": model, "fallback": ladder(model)[-1], "source_unit": unit_of(lines)},
            "context_ref": fetched.context_ref,
            "revision_ref": fetched.revision_ref,
            "transcript_sha256": fetched.sha256,
            "verification_status": fetched.verification_status,
            "recheck_outcome": outcome,
            "members": [
                m | {"index": v["index"], "from": v["from"], "to": v["to"]}
                for m, v in zip(fetched.members, volumes, strict=True)
            ],
        }
        if replace is not None:
            return self.store.replace(replace, fields)
        return self.store.add(fields)

    def card(self, card_id: str) -> dict[str, Any]:
        """The card with the transcript timeline when the cached text is still the same."""
        row = self.store.get(card_id)
        cached = None if row["withdrawn"] else self.library.cached(row["item_ref"])
        if cached is not None and cached.sha256 == row["transcript_sha256"]:
            row["timeline"] = timeline(row["card"], self._lines(cached))
        else:
            row["timeline"] = None
        return row

    def recheck(self, card_id: str) -> dict[str, Any]:
        row = self.store.get(card_id)
        # A series card is as usable as its least usable volume.
        targets = row["members"] or [row]
        outcomes = []
        for target in targets:
            try:
                outcomes.append(self.library.recheck(target["context_ref"], target["revision_ref"]))
            except LibraryError:
                outcomes.append("unknown")
        unusable = [o for o in outcomes if o not in USABLE]
        if unusable:
            outcome = unusable[0]
        else:
            outcome = "conditional" if "conditional" in outcomes else "still_usable"
        if outcome not in USABLE:
            self.library.forget(row["item_ref"])
            for target in row["members"]:
                self.library.forget(target["item_ref"])
            return self.store.rechecked(card_id, outcome)
        # The same source text: run the script checks again, so a fixed checker reaches cards
        # made before the fix (Korean units, 2026-11-16). Another text is not checked here.
        cached = self.library.cached(row["item_ref"])
        if cached is not None and cached.sha256 == row["transcript_sha256"]:
            checks = check_card(row["card"], self._lines(cached))
            if checks != row["checks"]:
                self.store.update_checks(card_id, checks)
        return self.store.rechecked(card_id, outcome)
