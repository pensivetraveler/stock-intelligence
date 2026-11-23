"""ADR 0056 L1 (library lookup, cache, recheck) and L2 (extraction, checks, store, routes).

No network and no library: the CLI runner and the model calls are fakes.
"""

from __future__ import annotations

import copy
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from services.api.technique_card_routes import register_technique_card_routes
from services.assistant import conversion
from services.technique_cards import extract
from services.technique_cards.checks import check_card, no_criterion, not_computable, timeline
from services.technique_cards.citations import (
    Line,
    join_volumes,
    parse_source,
    parse_transcript,
)
from services.technique_cards.extract import (
    AttemptFailed,
    ExtractionError,
    Keys,
    call_anthropic,
    call_openrouter,
    extract_card,
)
from services.technique_cards.library import Library, LibraryConfig, LibraryError
from services.technique_cards.service import TechniqueCards
from services.technique_cards.store import CardStore

ITEM = "kl://kl-lib/item/01M7CJ84TD615YWVWKWHDTKGH7"
OTHER = "kl://kl-lib/item/01M7CJ8558M34G7P7X05A8NA8M"
TRANSCRIPT_TEXT = (
    "[00:10] We draw one trend line, a fifty period moving average, an E M A.\n"
    "[00:50] When the price closes back above that line, we buy.\n"
    "[01:10] The stop sits two A T R below the entry price.\n"
    "[01:40] If the tools disagree, just wait and do not trade.\n"
    "[02:30] Volume should really surge on the breakout.\n"
)
LINES = parse_transcript(TRANSCRIPT_TEXT)
KEYS = Keys(anthropic="test-anthropic-not-real", openrouter="test-openrouter-not-real")


def _op(kind: str, name: str, period: float | None = None, value: float | None = None) -> dict:
    return {"kind": kind, "name": name, "period": period, "value": value}


def _condition(**over: Any) -> dict[str, Any]:
    base = {
        "field": "entry",
        "side": "long",
        "text": "Buy when the close crosses above the 50 EMA.",
        "basis": "source",
        "left": _op("price", "close"),
        "comparator": "cross_above",
        "right": _op("indicator", "ema", period=50),
        "timeframe": "",
        "citations": [{"locator": "00:50", "quote": "When the price closes back above"}],
        "value_citations": [
            {"value": 50, "locator": "00:10", "quote": "a fifty period moving average"}
        ],
    }
    return base | over


def _card() -> dict[str, Any]:
    return {
        "source_id": "demo",
        "title": "demo",
        "conditions": [
            _condition(),
            # RSI 14 was never said: a default the model filled in.
            _condition(
                text="RSI(14) above 50",
                right=_op("value", "none", value=50),
                left=_op("indicator", "rsi", period=14),
                comparator="above",
                value_citations=[],
            ),
            _condition(
                text="Volume surges",
                left=_op("price", "volume"),
                comparator="rises",
                right=_op("none", "none"),
                citations=[{"locator": "02:30", "quote": "Volume should really surge"}],
                value_citations=[],
            ),
            _condition(
                text="Skip when the tools disagree",
                left=_op("other", "other"),
                comparator="other",
                right=_op("none", "none"),
                citations=[{"locator": "01:40", "quote": "If the tools disagree"}],
                value_citations=[],
            ),
        ],
        "timeframe": [],
        "universe": [],
        "position": [],
        "inexpressible": [],
        "ambiguities": [
            {
                "text": "How much is a surge?",
                "question": "What volume ratio?",
                "citations": [{"locator": "02:30", "quote": "Volume should really surge"}],
            }
        ],
    }


# --- L1: library --------------------------------------------------------------------------


class FakeCli:
    """Answers kl.cli commands like the library does; records every call."""

    def __init__(self, recheck: str = "conditional") -> None:
        self.calls: list[tuple[list[str], dict[str, Any]]] = []
        self.recheck = recheck

    def __call__(self, args: list[str], request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((args, request))
        ok = {"status": "success", "reason_code": "OK"}
        if args[:2] == ["delivery", "list"]:
            if "--cursor" not in args:
                deliveries = [
                    {"item_ref": ITEM, "state": "sent", "sent_at": "2026-11-14T11:52:28Z"},
                    {"item_ref": OTHER, "state": "failed", "sent_at": "2026-11-14T11:52:29Z"},
                ]
                return ok | {"data": {"deliveries": deliveries, "next_cursor": "page2"}}
            deliveries = [{"item_ref": OTHER, "state": "sent", "sent_at": "2026-11-14T12:00:00Z"}]
            return ok | {"data": {"deliveries": deliveries, "next_cursor": None}}
        if args[:2] == ["item", "get"] and "item_refs" in request:
            return {"status": "error", "reason_code": "INVALID_REQUEST", "data": None}  # pre-0.5.1
        if args[:2] == ["item", "get"]:
            tags = [{"name": "매매기법"}] if request["item_ref"] == ITEM else [{"name": "요리"}]
            item = {"title": "Demo", "url": "https://example.test/v", "source_key": "demo"}
            return ok | {
                "data": {
                    "item": item | {"state": "current", "tags": tags},
                    "members": [
                        {
                            "artifact_ref": "kl://kl-lib/artifact/A",
                            "eligibility_outcome": "conditional",
                        },
                        {
                            "artifact_ref": "kl://kl-lib/artifact/B",
                            "eligibility_outcome": "conditional",
                        },
                    ],
                }
            }
        if args[:2] == ["context", "assemble"]:
            selected = [
                {"revision_ref": "kl://kl-lib/revision/S"},
                {"revision_ref": "kl://kl-lib/revision/T"},
            ]
            return ok | {
                "data": {"context_ref": "kl://kl-lib/context/C", "selected_items": selected}
            }
        if args[:2] == ["context", "hydrate"]:
            out = Path(args[args.index("--out-dir") + 1])
            (out / "P1").write_text("A summary without times.")
            (out / "P1.meta.json").write_text(
                json.dumps({"revision_ref": "kl://kl-lib/revision/S"})
            )
            (out / "P2").write_text(TRANSCRIPT_TEXT)
            (out / "P2.meta.json").write_text(
                json.dumps(
                    {
                        "revision_ref": "kl://kl-lib/revision/T",
                        "verification_status": "unverified",
                        "data_grade": "private",
                    }
                )
            )
            return ok | {"data": {"delivered_items": []}}
        if args[:2] == ["context", "hydration-check"]:
            return ok | {"data": {"outcome": self.recheck}}
        return {"status": "error", "reason_code": "INVALID_REQUEST", "data": None}


def _config() -> LibraryConfig:
    return LibraryConfig(Path("/nowhere"), "kl://kl-lib/task/T", "kl://kl-lib/tagsubscription/S")


def test_config_needs_all_three_settings() -> None:
    assert LibraryConfig.from_env({"SIW_KL_ROOT": "/x"}) is None
    config = LibraryConfig.from_env(
        {
            "SIW_KL_ROOT": "/x",
            "SIW_KL_TASK": "kl://kl-lib/task/T",
            "SIW_KL_TAGSUBSCRIPTION": "kl://kl-lib/tagsubscription/S",
            "HOME": "/home/u",
        }
    )
    assert config is not None
    assert config.env["KL_PG_DB"] == "kl_lib"
    assert config.env["KL_BLOB_DIR"] == "/home/u/.local/share/knowledge-library/kl-lib/blobs"


def test_sources_page_to_the_end_and_keep_only_tagged_sent_items(tmp_path: Path) -> None:
    cli = FakeCli()
    sources = Library(_config(), tmp_path, cli).sources()
    assert [s.item_ref for s in sources] == [ITEM]  # OTHER was sent but carries no 매매기법 tag
    assert sum(args[:2] == ["delivery", "list"] for args, _ in cli.calls) == 2


def test_error_envelope_is_raised_without_text(tmp_path: Path) -> None:
    library = Library(
        _config(), tmp_path, lambda a, r: {"status": "error", "reason_code": "NOT_AVAILABLE"}
    )
    with pytest.raises(LibraryError) as caught:
        library.sources()
    assert caught.value.reason_code == "NOT_AVAILABLE"


def test_fetch_keeps_the_timed_layer_private_and_recheck_reuses_it(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    library = Library(_config(), cache, FakeCli())
    fetched = library.fetch(ITEM)
    assert fetched.revision_ref == "kl://kl-lib/revision/T"
    assert fetched.path.read_text() == TRANSCRIPT_TEXT
    assert fetched.path.stat().st_mode & 0o777 == 0o600
    assert fetched.path.parent.stat().st_mode & 0o777 == 0o700
    assert not (fetched.path.parent / "hydrated").exists()
    assert library.cached(ITEM) == fetched
    fetched.path.write_text("tampered")
    assert library.cached(ITEM) is None
    library.forget(ITEM)
    assert not fetched.path.parent.exists()


def test_service_deletes_the_cache_when_the_library_says_stop(tmp_path: Path) -> None:
    store = CardStore(
        tmp_path / "cards.sqlite3", lambda: datetime(2026, 11, 14, tzinfo=timezone.utc)
    )
    service = TechniqueCards(Library(_config(), tmp_path / "cache", FakeCli("stop_using")), store)
    source = service.library.source(ITEM)
    assert source is not None
    with pytest.raises(RuntimeError, match="stop_using"):
        service.preview(source)
    assert service.library.cached(ITEM) is None


def test_two_previews_of_one_item_at_once_fetch_it_once(tmp_path: Path) -> None:
    """2026-11-22: concurrent previews deleted each other's folder; one cached a metadata note."""

    class SlowCli(FakeCli):
        def __call__(self, args: list[str], request: dict[str, Any]) -> dict[str, Any]:
            if args[:2] == ["context", "hydrate"]:
                out = Path(args[args.index("--out-dir") + 1])
                (out / "P1").write_text("A summary without times.")
                (out / "P1.meta.json").write_text(
                    json.dumps({"revision_ref": "kl://kl-lib/revision/S"})
                )
                time.sleep(0.2)  # the second request arrives while the first is hydrating
            return super().__call__(args, request)

    cli = SlowCli()
    store = CardStore(
        tmp_path / "cards.sqlite3", lambda: datetime(2026, 11, 22, tzinfo=timezone.utc)
    )
    service = TechniqueCards(Library(_config(), tmp_path / "cache", cli), store)
    source = service.library.source(ITEM)
    assert source is not None
    with ThreadPoolExecutor(max_workers=2) as pool:
        previews = list(pool.map(lambda _: service.preview(source), range(2)))
    assert [p["lines"] for p in previews] == [previews[0]["lines"]] * 2
    assert sum(args[:2] == ["context", "hydrate"] for args, _ in cli.calls) == 1
    cached = service.library.cached(ITEM)
    assert cached is not None and cached.path.read_text() == TRANSCRIPT_TEXT
    assert [p.name for p in (tmp_path / "cache").iterdir() if p.name.startswith(".")] == []


def test_a_failed_fetch_keeps_the_previous_cache(tmp_path: Path) -> None:
    cli = FakeCli()
    library = Library(_config(), tmp_path / "cache", cli)
    before = library.fetch(ITEM)

    def broken(args: list[str], request: dict[str, Any]) -> dict[str, Any]:
        if args[:2] == ["context", "hydrate"]:
            return {"status": "error", "reason_code": "NOT_AVAILABLE"}
        return cli(args, request)

    library._run = broken
    with pytest.raises(LibraryError):
        library.fetch(ITEM)
    assert library.cached(ITEM) == before
    assert [p.name for p in (tmp_path / "cache").iterdir() if p.name.startswith(".")] == []


# --- series (#SIW-lib-23, user 2026-11-22) -------------------------------------------------

V1 = "kl://kl-lib/item/V1"
V2 = "kl://kl-lib/item/V2"
SOLO = "kl://kl-lib/item/SOLO"
VOLUME_TEXT = {
    V1: "[p. 1]\nBuy the first pullback.\n[p. 2]\nStop below the low.\n",
    V2: "[p. 1]\nSell into strength.\n",
}


class SeriesCli:
    """Two volumes of one series (given out of order) and one book alone."""

    def __init__(self, outcomes: dict[str, str] | None = None) -> None:
        self.outcomes = outcomes or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], request: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(args)
        ok = {"status": "success", "reason_code": "OK"}
        if args[:2] == ["delivery", "list"]:
            deliveries = [
                {"item_ref": ref, "state": "sent", "sent_at": f"2026-11-15T15:3{n}:00Z"}
                for n, ref in enumerate((V2, SOLO, V1))
            ]
            return ok | {"data": {"deliveries": deliveries, "next_cursor": None}}
        if args[:2] == ["item", "get"] and "item_refs" in request:
            return {"status": "error", "reason_code": "INVALID_REQUEST", "data": None}  # pre-0.5.1
        if args[:2] == ["item", "get"]:
            ref = request["item_ref"]
            series = {V1: {"name": "원펀치", "index": 1}, V2: {"name": "원펀치", "index": 2}}
            item = {
                "title": ref.rsplit("/", 1)[1],
                "url": "",
                "source_key": ref,
                "state": "current",
                "tags": [{"name": "매매기법"}],
                "series": series.get(ref),
            }
            members = [{"artifact_ref": f"{ref}#a", "eligibility_outcome": "conditional"}]
            return ok | {"data": {"item": item, "members": members}}
        if args[:2] == ["context", "assemble"]:
            ref = request["candidate_artifact_refs"][0].split("#")[0]
            return ok | {
                "data": {
                    "context_ref": f"ctx:{ref}",
                    "selected_items": [{"revision_ref": f"rev:{ref}"}],
                }
            }
        if args[:2] == ["context", "hydrate"]:
            ref = request["context_ref"].removeprefix("ctx:")
            out = Path(args[args.index("--out-dir") + 1])
            (out / "T").write_text(VOLUME_TEXT.get(ref, "[p. 1]\nAlone.\n"))
            (out / "T.meta.json").write_text(json.dumps({"revision_ref": f"rev:{ref}"}))
            return ok | {"data": {}}
        if args[:2] == ["context", "hydration-check"]:
            ref = request["revision_ref"].removeprefix("rev:")
            return ok | {"data": {"outcome": self.outcomes.get(ref, "still_usable")}}
        return {"status": "error", "reason_code": "INVALID_REQUEST", "data": None}


def _series_service(tmp_path: Path, cli: SeriesCli) -> TechniqueCards:
    store = CardStore(
        tmp_path / "cards.sqlite3", lambda: datetime(2026, 11, 22, tzinfo=timezone.utc)
    )
    return TechniqueCards(Library(_config(), tmp_path / "cache", cli), store)


def test_join_volumes_numbers_pages_and_paragraphs_on() -> None:
    text, ranges = join_volumes(["[p. 1]\na\n[p. 2]\nb\n", "[p. 1]\nc\n[p. 3]\nd\n"])
    assert [(line.seconds, line.text) for line in parse_source(text)] == [
        (1, "a"),
        (2, "b"),
        (3, "c"),
        (5, "d"),
    ]
    assert ranges == [(1, 2), (3, 5)]
    text, ranges = join_volumes(["one\n\ntwo", "# three\nfour"])
    assert [line.text for line in parse_source(text)] == ["one", "two", "# three", "four"]
    assert ranges == [(1, 2), (3, 4)]
    text, ranges = join_volumes(["[00:00] a\n[00:10] b", "[00:00] c"])
    assert [line.seconds for line in parse_source(text)] == [0, 10, 11]
    with pytest.raises(ValueError):
        join_volumes(["[p. 1]\na", "plain text"])


def test_the_list_shows_a_series_once_with_its_volumes_in_order(tmp_path: Path) -> None:
    sources = Library(_config(), tmp_path, SeriesCli()).sources()
    assert [s.item_ref for s in sources] == ["kl-series:원펀치", SOLO]
    series = sources[0]
    assert [v.item_ref for v in series.members] == [V1, V2]
    assert series.to_json()["series"]["volumes"][1] == {"item_ref": V2, "title": "V2", "index": 2}
    assert "series" not in sources[1].to_json()


def test_a_series_previews_and_cards_as_one_source(tmp_path: Path) -> None:
    service = _series_service(tmp_path, SeriesCli({V2: "conditional"}))
    source = service.source("kl-series:원펀치")
    assert source is not None and source.is_series
    preview = service.preview(source)
    assert preview["lines"] == 3
    assert preview["source_unit"] == "page"
    assert preview["recheck_outcome"] == "conditional"
    assert [(v["index"], v["from"], v["to"]) for v in preview["volumes"]] == [
        (1, "p. 1", "p. 2"),
        (2, "p. 3", "p. 3"),
    ]
    assert TechniqueCards._prompt_title(source, preview["volumes"]) == (
        "원펀치 (시리즈 2권: 1권 p. 1–p. 2, 2권 p. 3–p. 3)"
    )
    cached = service.library.cached("kl-series:원펀치")
    assert cached is not None
    assert [m["item_ref"] for m in cached.members] == [V1, V2]
    assert [line.seconds for line in parse_source(cached.path.read_text())] == [1, 2, 3]


def test_a_series_card_is_withdrawn_when_any_volume_is(tmp_path: Path) -> None:
    cli = SeriesCli()
    service = _series_service(tmp_path, cli)
    source = service.source("kl-series:원펀치")
    assert source is not None
    fetched, outcome, volumes = service._usable_source(source)
    card_id = service.store.add(
        {
            "item_ref": source.item_ref,
            "title": source.title,
            "url": "",
            "card": {},
            "checks": {"conditions": [], "counts": {}},
            "usage": {},
            "attempts": [],
            "versions": {},
            "context_ref": fetched.context_ref,
            "revision_ref": fetched.revision_ref,
            "transcript_sha256": fetched.sha256,
            "verification_status": fetched.verification_status,
            "recheck_outcome": outcome,
            "members": [
                m | {"index": v["index"]} for m, v in zip(fetched.members, volumes, strict=True)
            ],
        }
    )
    assert [m["item_ref"] for m in service.store.get(card_id)["members"]] == [V1, V2]
    assert service.recheck(card_id)["recheck_outcome"] == "still_usable"
    cli.outcomes[V2] = "stop_using"
    assert service.recheck(card_id)["recheck_outcome"] == "stop_using"
    assert service.library.cached("kl-series:원펀치") is None
    assert service.library.cached(V2) is None


def test_series_routes_preview_and_extract_one_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """2026-11-23: the route refused `kl-series:` refs (422) though the service took them."""
    service = _series_service(tmp_path, SeriesCli())
    app = FastAPI()

    def error(request: Any, status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": {"code": code}})

    register_technique_card_routes(
        app, cards=lambda: service, keys=lambda: KEYS, error_response=error, start=lambda w: w()
    )
    client = TestClient(app)

    def fake_extract(source_id, title, lines, keys, call=None, transport=None, model="", **_):  # type: ignore[no-untyped-def]
        assert title.startswith("원펀치 (시리즈 2권:")
        return extract.Extraction(card=_card(), attempts=[])

    monkeypatch.setattr("services.technique_cards.service.extract_card", fake_extract)
    listed = client.get("/v1/technique-sources").json()["sources"]
    assert [s["item_ref"] for s in listed] == ["kl-series:원펀치", SOLO]
    preview = client.post("/v1/technique-sources/preview", json={"item_ref": "kl-series:원펀치"})
    assert preview.status_code == 200
    assert [v["index"] for v in preview.json()["volumes"]] == [1, 2]
    started = client.post("/v1/technique-cards", json={"item_ref": "kl-series:원펀치"})
    assert started.status_code == 202
    job = client.get(f"/v1/technique-cards/jobs/{started.json()['job_id']}").json()
    card = service.store.get(job["card_id"])
    assert card["item_ref"] == "kl-series:원펀치"
    assert [(m["item_ref"], m["index"]) for m in card["members"]] == [(V1, 1), (V2, 2)]
    missing = client.post("/v1/technique-sources/preview", json={"item_ref": "kl-series:없음"})
    assert missing.status_code == 404
    bad = client.post("/v1/technique-sources/preview", json={"item_ref": "kl-series:"})
    assert bad.status_code == 422


def test_a_decomposed_series_name_is_kept_composed(tmp_path: Path) -> None:
    """2026-11-23: the library sent the name in NFD (a macOS file name); an NFC ref got 404."""
    import unicodedata

    class NfdCli(SeriesCli):
        def __call__(self, args: list[str], request: dict[str, Any]) -> dict[str, Any]:
            answer = super().__call__(args, request)
            series = (answer.get("data") or {}).get("item", {}).get("series")
            if args[:2] == ["item", "get"] and series:
                series["name"] = unicodedata.normalize("NFD", "원펀치매매")
            return answer

    service = _series_service(tmp_path, NfdCli())
    listed = service.sources()
    assert listed[0].item_ref == "kl-series:원펀치매매"
    assert unicodedata.is_normalized("NFC", listed[0].item_ref)
    for ref in ("kl-series:원펀치매매", unicodedata.normalize("NFD", "kl-series:원펀치매매")):
        source = service.source(ref)
        assert source is not None and len(source.members) == 2


class BatchCli(SeriesCli):
    """A library on contract 0.5.1: `item_refs` lookups and layer names on members."""

    def __init__(self) -> None:
        super().__init__()
        self.assembled: list[list[str]] = []

    def __call__(self, args: list[str], request: dict[str, Any]) -> dict[str, Any]:
        if args[:2] == ["item", "get"] and "item_refs" in request:
            self.calls.append(args + ["<batch>"])
            items = []
            for ref in request["item_refs"]:
                if ref == SOLO:
                    items.append(None)  # gone or not visible
                    continue
                single = super().__call__(["item", "get"], {"item_ref": ref})
                self.calls.pop()
                items.append(single["data"]["item"])
            return {"status": "success", "reason_code": "OK", "data": {"items": items}}
        if args[:2] == ["item", "get"]:
            answer = super().__call__(args, request)
            ref = request["item_ref"]
            answer["data"]["members"] = [
                {"artifact_ref": f"{ref}#pdf", "layers": ["pdf_original"]},
                {"artifact_ref": f"{ref}#a", "layers": ["pdf_text"]},
                {"artifact_ref": f"{ref}#sum", "layers": ["summary"]},
            ]
            return answer
        if args[:2] == ["context", "assemble"]:
            self.assembled.append(list(request["candidate_artifact_refs"]))
        return super().__call__(args, request)


def test_the_list_asks_for_all_items_in_one_call(tmp_path: Path) -> None:
    cli = BatchCli()
    sources = Library(_config(), tmp_path, cli).sources()
    assert [s.item_ref for s in sources] == ["kl-series:원펀치"]  # SOLO came back null
    item_calls = [c for c in cli.calls if c[:2] == ["item", "get"]]
    assert item_calls == [["item", "get", "<batch>"]]


def test_a_library_without_item_refs_is_asked_item_by_item(tmp_path: Path) -> None:
    cli = SeriesCli()  # answers INVALID_REQUEST to item_refs
    sources = Library(_config(), tmp_path, cli).sources()
    assert [s.item_ref for s in sources] == ["kl-series:원펀치", SOLO]
    assert sum(c[:2] == ["item", "get"] for c in cli.calls) == 1 + 3


def test_only_the_pdf_text_layer_is_hydrated_when_layers_are_named(tmp_path: Path) -> None:
    cli = BatchCli()
    fetched = Library(_config(), tmp_path / "cache", cli).fetch(V1)
    assert cli.assembled == [[f"{V1}#a"]]
    assert fetched.path.read_text() == VOLUME_TEXT[V1]


def test_a_value_siw_cannot_compute_is_not_called_criterionless() -> None:
    """2026-11-23 card 841c304b: '거래대금 50억 이상' and '5일 상승률 +20%' had a number but were
    flagged 기준 없음 because the model put kind `other` on the left side."""
    trading_value = _condition(
        left=_op("other", "trading_value", period=1),
        comparator="at_or_above",
        right=_op("value", "none", value=5_000_000_000),
    )
    assert no_criterion(trading_value) == "" and not_computable(trading_value) == ""
    five_day_return = _condition(
        left=_op("other", "other", period=5),
        comparator="below",
        right=_op("percent", "none", value=20),
    )
    assert no_criterion(five_day_return) == ""
    assert not_computable(five_day_return) == "the compared value is not one SIW computes"
    nothing = _condition(left=_op("none", "none"), comparator="above")
    assert no_criterion(nothing) == "nothing compared" and not_computable(nothing) == ""
    vague = _condition(left=_op("other", "other"), comparator="other", right=_op("none", "none"))
    assert no_criterion(vague) == "no comparison" and not_computable(vague) == ""
    checks = check_card({"conditions": [trading_value, five_day_return, vague]}, [])
    codes = [
        [f["code"] for f in c["flags"] if f["code"] != "unbacked"] for c in checks["conditions"]
    ]
    assert codes == [[], ["not_computable"], ["no_criterion"]]
    assert checks["counts"]["not_computable"] == 1 and checks["counts"]["no_criterion"] == 1


# --- L2: extraction -----------------------------------------------------------------------


def test_ladder_retries_sonnet_then_luna_and_logs_every_call() -> None:
    routes: list[str] = []

    def caller(route: str, system: str, prompt: str, keys: Keys) -> tuple[dict, dict]:
        routes.append(route)
        if route == "claude-sonnet-5-5":
            raise AttemptFailed(
                "length", {"input_tokens": 100, "output_tokens": 16000, "cost_usd": "0.16"}
            )
        return _card(), {"input_tokens": 100, "output_tokens": 3000, "cost_usd": "0.01"}

    result = extract_card("demo", "demo", LINES, KEYS, call=caller, model="claude-sonnet-5-5")
    assert routes == ["claude-sonnet-5-5", "claude-sonnet-5-5", "openai/gpt-6-luna"]
    assert [a["ok"] for a in result.attempts] == [False, False, True]
    assert result.usage["cost_usd"] == "0.33"
    assert result.usage["models"] == ["openai/gpt-6-luna"]


def test_all_models_failing_raises_with_the_attempts() -> None:
    def caller(route: str, system: str, prompt: str, keys: Keys) -> tuple[dict, dict]:
        raise AttemptFailed("schema: bad")

    with pytest.raises(ExtractionError) as caught:
        extract_card("demo", "demo", LINES, KEYS, call=caller)
    assert len(caught.value.attempts) == 3


def test_long_source_is_called_per_slice_and_merged() -> None:
    long_lines = [Line(m * 60, f"line {m}") for m in range(60)]
    prompts: list[str] = []

    def caller(route: str, system: str, prompt: str, keys: Keys) -> tuple[dict, dict]:
        prompts.append(prompt)
        return _card(), {"input_tokens": 1, "output_tokens": 1, "cost_usd": "0"}

    result = extract_card("demo", "demo", long_lines, KEYS, call=caller)
    assert result.parts == len(prompts) == 3
    assert "part 1 of 3" in prompts[0]
    assert len(result.card["conditions"]) == len(_card()["conditions"])  # overlap repeats dropped


def _anthropic_transport(
    message: dict[str, Any], seen: list[dict[str, Any]]
) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content) | {"_headers": dict(request.headers)})
        return httpx.Response(200, json=message)

    return httpx.MockTransport(handle)


def test_anthropic_call_is_strict_and_priced() -> None:
    seen: list[dict[str, Any]] = []
    message = {
        "stop_reason": "end_turn",
        "content": [{"type": "text", "text": json.dumps(_card())}],
        "usage": {"input_tokens": 1_000_000, "output_tokens": 100_000},
    }
    card, usage = call_anthropic("sys", "prompt", KEYS, _anthropic_transport(message, seen))
    assert card["source_id"] == "demo"
    assert Decimal(usage["cost_usd"]) == Decimal("3")  # $2 in + $1 out
    body = seen[0]
    assert body["model"] == "claude-sonnet-5-5" and body["max_tokens"] == 16000
    assert body["output_config"]["format"]["type"] == "json_schema"
    with pytest.raises(AttemptFailed, match="length"):
        call_anthropic(
            "sys", "p", KEYS, _anthropic_transport(message | {"stop_reason": "max_tokens"}, [])
        )
    with pytest.raises(AttemptFailed, match="schema"):
        bad = message | {"content": [{"type": "text", "text": "{}"}]}
        call_anthropic("sys", "p", KEYS, _anthropic_transport(bad, []))
    with pytest.raises(AttemptFailed, match="no Claude"):
        call_anthropic("sys", "p", Keys(None, None))


def test_openrouter_call_denies_data_collection() -> None:
    seen: list[dict[str, Any]] = []
    message = {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(_card())}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.0123},
    }
    _card_out, usage = call_openrouter("sys", "p", KEYS, _anthropic_transport(message, seen))
    assert usage == {"input_tokens": 10, "output_tokens": 20, "cost_usd": "0.0123"}
    assert seen[0]["model"] == "openai/gpt-6-luna"
    assert seen[0]["provider"]["data_collection"] == "deny"
    assert seen[0]["response_format"]["json_schema"]["strict"] is True
    assert seen[0]["_headers"]["authorization"] == "Bearer test-openrouter-not-real"


# --- L2: checks ---------------------------------------------------------------------------


def test_checks_flag_unbacked_numbers_and_missing_criteria() -> None:
    checks = check_card(_card(), LINES)
    flags = {c["index"]: [f["code"] for f in c["flags"]] for c in checks["conditions"]}
    assert flags[0] == []
    assert "unbacked" in flags[1]  # RSI 14 and 50 never said
    assert flags[2] == []  # "volume rises" names a direction; the ratio is the ambiguity
    assert "no_criterion" in flags[3]
    assert checks["counts"]["unbacked"] == 1 and checks["counts"]["no_criterion"] == 1
    assert no_criterion(_condition(right=_op("value", "none", value=None), comparator="above"))


def test_timeline_marks_the_lines_each_condition_cites() -> None:
    rows = timeline(_card(), LINES)
    assert [r["time"] for r in rows] == ["00:10", "00:50", "01:10", "01:40", "02:30"]
    assert rows[1]["conditions"] == [0, 1]
    assert rows[2]["conditions"] == []  # nothing on the card cites the stop line: left out


# --- store and routes ---------------------------------------------------------------------


def _app(tmp_path: Path, recheck: str = "conditional") -> tuple[TestClient, TechniqueCards]:
    store = CardStore(
        tmp_path / "cards.sqlite3", lambda: datetime(2026, 11, 14, tzinfo=timezone.utc)
    )
    service = TechniqueCards(Library(_config(), tmp_path / "cache", FakeCli(recheck)), store)
    app = FastAPI()

    def error(request: Any, status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status, content={"error": {"code": code, "message": message}}
        )

    register_technique_card_routes(
        app, cards=lambda: service, keys=lambda: KEYS, error_response=error, start=lambda w: w()
    )
    return TestClient(app), service


def test_routes_extract_check_resolve_and_recheck(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _app(tmp_path)

    def fake_extract(source_id, title, lines, keys, call=None, transport=None, model="", **_):  # type: ignore[no-untyped-def]
        return extract.Extraction(
            card=_card(),
            attempts=[
                {
                    "part": 1,
                    "model": "claude-sonnet-5-5",
                    "ok": True,
                    "cost_usd": "0.2",
                    "input_tokens": 9,
                    "output_tokens": 9,
                }
            ],
        )

    monkeypatch.setattr("services.technique_cards.service.extract_card", fake_extract)
    listed = client.get("/v1/technique-sources").json()
    assert [s["item_ref"] for s in listed["sources"]] == [ITEM]
    preview = client.post("/v1/technique-sources/preview", json={"item_ref": ITEM}).json()
    assert preview["parts"] == 1 and preview["model"] == "google/gemini-4-argon"
    assert "surge" not in json.dumps(preview)  # the preview says sizes, not the text
    assert client.post("/v1/technique-sources/preview", json={"item_ref": OTHER}).status_code == 404

    started = client.post("/v1/technique-cards", json={"item_ref": ITEM})
    assert started.status_code == 202
    job = client.get(f"/v1/technique-cards/jobs/{started.json()['job_id']}").json()
    assert job["state"] == "done", job
    card_id = job["card_id"]

    card = client.get(f"/v1/technique-cards/{card_id}").json()
    assert card["ready"] is False
    assert card["blocks"] == ["condition 1 not resolved", "condition 3 not resolved"]
    assert card["usage"]["flags"]["unbacked"] == 1
    assert card["timeline"][1]["conditions"] == [0, 1]

    bad = client.post(
        f"/v1/technique-cards/{card_id}/resolutions",
        json={"version": 1, "conditions": {"9": {"action": "drop"}}},
    )
    assert bad.status_code == 422
    bad_edit = client.post(
        f"/v1/technique-cards/{card_id}/resolutions",
        json={"version": 1, "conditions": {"1": {"action": "edit", "condition": {"x": 1}}}},
    )
    assert bad_edit.status_code == 422
    edited = copy.deepcopy(_card()["conditions"][1]) | {"left": _op("indicator", "rsi", period=2)}
    saved = client.post(
        f"/v1/technique-cards/{card_id}/resolutions",
        json={
            "version": 1,
            "conditions": {"1": {"action": "edit", "condition": edited}, "3": {"action": "drop"}},
            "ambiguities": {"0": "2배"},
            "added": [{"field": "entry", "text": " 거래량 증가 "}, {"field": "exit", "text": " "}],
        },
    )
    assert saved.status_code == 200, saved.json()
    assert saved.json()["ready"] is True and saved.json()["corrected"] == 2
    assert service.store.get(card_id)["resolutions"]["added"] == [
        {"field": "entry", "text": "거래량 증가"}
    ]
    bad_added = client.post(
        f"/v1/technique-cards/{card_id}/resolutions",
        json={"version": 2, "added": [{"field": "stop_loss", "text": "x"}]},
    )
    assert bad_added.status_code == 422
    stale = client.post(f"/v1/technique-cards/{card_id}/resolutions", json={"version": 1})
    assert stale.status_code == 409

    service.library = Library(_config(), tmp_path / "cache", FakeCli("stop_using"))
    rechecked = client.post(f"/v1/technique-cards/{card_id}/recheck").json()
    assert rechecked["withdrawn"] is True and rechecked["ready"] is False
    assert service.library.cached(ITEM) is None
    assert client.get(f"/v1/technique-cards/{card_id}").json()["timeline"] is None


def test_failed_extraction_is_a_failed_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _service = _app(tmp_path)

    def failing(*args: Any, **kwargs: Any) -> None:
        raise ExtractionError("slice 1 of 1: no model gave a valid card", [{"model": "m"}])

    monkeypatch.setattr("services.technique_cards.service.extract_card", failing)
    job_id = client.post("/v1/technique-cards", json={"item_ref": ITEM}).json()["job_id"]
    job = client.get(f"/v1/technique-cards/jobs/{job_id}").json()
    assert job["state"] == "failed" and "no model" in job["error"]
    assert job["attempts"] == [{"model": "m"}]


def test_a_chosen_model_runs_twice_then_luna_and_unknown_models_are_refused() -> None:
    models: list[str] = []

    def caller(model: str, system: str, prompt: str, keys: Keys) -> tuple[dict, dict]:
        models.append(model)
        if model == "google/gemini-4-argon":
            raise AttemptFailed("schema: bad")
        return _card(), {"input_tokens": 1, "output_tokens": 1, "cost_usd": "0"}

    result = extract_card("demo", "demo", LINES, KEYS, call=caller, model="google/gemini-4-argon")
    assert models == ["google/gemini-4-argon", "google/gemini-4-argon", "openai/gpt-6-luna"]
    assert result.usage["models"] == ["openai/gpt-6-luna"]
    assert extract.ladder("openai/gpt-6-luna") == ("openai/gpt-6-luna", "openai/gpt-6-luna")
    with pytest.raises(ValueError):
        extract.ladder("deepseek/anything")


def test_openrouter_call_sends_the_chosen_model() -> None:
    seen: list[dict[str, Any]] = []
    message = {
        "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(_card())}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.001},
    }
    call_openrouter(
        "sys", "p", KEYS, _anthropic_transport(message, seen), model="google/gemini-4-argon"
    )
    assert seen[0]["model"] == "google/gemini-4-argon"
    assert seen[0]["provider"]["data_collection"] == "deny"


def test_routes_pass_the_model_and_refuse_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _service = _app(tmp_path)
    used: list[str] = []

    def fake_extract(source_id, title, lines, keys, call=None, transport=None, model="", **_):  # type: ignore[no-untyped-def]
        used.append(model)
        return extract.Extraction(card=_card(), attempts=[{"model": model, "ok": True}])

    monkeypatch.setattr("services.technique_cards.service.extract_card", fake_extract)
    body = {"item_ref": ITEM, "model": "google/gemini-4-argon"}
    preview = client.post("/v1/technique-sources/preview", json=body).json()
    assert preview["model"] == "google/gemini-4-argon"
    assert preview["fallback"] == "google/gemini-4-argon → openai/gpt-6-luna"
    job_id = client.post("/v1/technique-cards", json=body).json()["job_id"]
    card_id = client.get(f"/v1/technique-cards/jobs/{job_id}").json()["card_id"]
    assert used == ["google/gemini-4-argon"]
    assert client.get(f"/v1/technique-cards/{card_id}").json()["versions"]["model"] == (
        "google/gemini-4-argon"
    )
    refused = client.post("/v1/technique-cards", json={"item_ref": ITEM, "model": "x/y"})
    assert refused.status_code == 422


# --- L3: strategy drafts ------------------------------------------------------------------

from services.technique_cards import strategy_draft  # noqa: E402

DONCHIAN = {
    "type": "rules",
    "entry": {
        "op": "gt",
        "left": {"price": "close"},
        "right": {"indicator": "dc", "params": {"length": 20}, "output": "upper", "offset": 1},
    },
    "exit": {
        "op": "lt",
        "left": {"price": "close"},
        "right": {"indicator": "dc", "params": {"length": 10}, "output": "lower", "offset": 1},
    },
}


def _ready_card(store: CardStore) -> dict[str, Any]:
    card = _card()
    card["position"] = [{"text": "Bet small", "basis": "source", "citations": []}]
    card["conditions"] = [
        # Slots the script turns into DONCHIAN's rules (script first, 2026-11-15).
        _condition(text="Buy above the 20-day high", comparator="above",
                   right=_op("price", "highest_high", period=20)),
        _condition(field="exit", text="Sell under the 10-day low", comparator="below",
                   right=_op("price", "lowest_low", period=10)),
        card["conditions"][3],  # "skip when the tools disagree": the user drops it
    ]  # fmt: skip
    card_id = store.add(
        {
            "item_ref": ITEM, "title": "Demo", "url": "u", "card": card,
            "checks": check_card(card, LINES), "usage": {}, "attempts": [], "versions": {},
            "context_ref": "c", "revision_ref": "r", "transcript_sha256": "s",
            "verification_status": "unverified", "recheck_outcome": "conditional",
        }
    )  # fmt: skip
    conditions = {
        str(c["index"]): {"action": "confirm", "condition": None, "note": ""}
        for c in check_card(card, LINES)["conditions"]
        if c["flags"]
    }
    conditions["2"] = {"action": "drop", "condition": None, "note": ""}
    return store.resolve(card_id, 1, conditions, {"0": "2x"})


def _reply(definition: dict[str, Any] | None, mapping: list[dict[str, Any]]) -> dict[str, Any]:
    answer = {
        "definition_json": json.dumps(definition) if definition else "",
        "mapping": mapping,
        "notes": "",
    }
    return {"content": [{"type": "text", "text": json.dumps(answer)}], "stop_reason": "end_turn"}


def _store(tmp_path: Path) -> CardStore:
    return CardStore(
        tmp_path / "cards.sqlite3", lambda: datetime(2026, 11, 15, tzinfo=timezone.utc)
    )


def test_card_items_skip_dropped_conditions_and_add_positions(tmp_path: Path) -> None:
    row = _ready_card(_store(tmp_path))
    items = strategy_draft.card_items(row)
    assert len(items) == 3
    assert items[0].startswith("[entry, long, from the source]")
    assert items[2] == "[position] Bet small"
    assert "2x" in strategy_draft.request_text(row, items)


def test_draft_maps_every_item_and_marks_unconvertible(tmp_path: Path) -> None:
    row = _ready_card(_store(tmp_path))
    seen: list[list[dict[str, Any]]] = []

    def call(system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        seen.append(messages)
        assert "technique card" in system and "dc" in system
        return _reply(
            DONCHIAN,
            [
                {"index": 0, "status": "mapped", "rule_paths": ["/entry"], "reason": ""},
                {"index": 1, "status": "mapped", "rule_paths": ["/exit"], "reason": ""},
                {"index": 2, "status": "unconvertible", "rule_paths": [], "reason": "no size"},
            ],
        )

    result = strategy_draft.draft(row, "google/gemini-4-argon", KEYS, call=call)
    assert result["conversion"]["definition"]["entry"]["right"]["offset"] == 1
    assert result["unconvertible"] == 1 and result["blocking"] == []
    assert "Numbered card items" in seen[0][0]["content"]
    assert strategy_draft.save_blocks(result, row, accept=False) == [
        "1 item(s) cannot be expressed: tick to leave out"
    ]
    assert strategy_draft.save_blocks(result, row, accept=True) == []


def test_draft_with_an_invented_condition_is_blocked(tmp_path: Path) -> None:
    row = _ready_card(_store(tmp_path))
    invented = {**DONCHIAN, "stop_loss_pct": "5"}

    def call(system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        return _reply(
            invented,
            [
                {"index": 0, "status": "mapped", "rule_paths": ["/entry"], "reason": ""},
                {"index": 1, "status": "mapped", "rule_paths": ["/exit"], "reason": ""},
                {"index": 2, "status": "unconvertible", "rule_paths": [], "reason": "no size"},
            ],
        )

    result = strategy_draft.draft(row, "google/gemini-4-argon", KEYS, call=call)
    assert any("/stop_loss_pct" in p for p in result["blocking"])
    assert strategy_draft.save_blocks(result, row, accept=True)


def test_blank_pointers_are_ignored_and_not_used_items_are_absent(tmp_path: Path) -> None:
    """The Argon run of 2026-11-15: "" padded every list and "no target" named the unset field."""
    row = _ready_card(_store(tmp_path))

    def call(system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
        assert '"absent"' in system and '"/take_profit_pct"' in system
        return _reply(
            DONCHIAN,
            [
                {"index": 0, "status": "mapped", "rule_paths": ["/entry", ""], "reason": ""},
                {"index": 1, "status": "mapped", "rule_paths": ["", "/exit"], "reason": ""},
                {"index": 2, "status": "absent", "rule_paths": ["/take_profit_pct"],
                 "reason": "목표가 없음"},
            ],
        )  # fmt: skip

    result = strategy_draft.draft(row, "google/gemini-4-argon", KEYS, call=call)
    assert result["blocking"] == [] and result["unconvertible"] == 0
    assert result["conversion"]["mapping"][0]["rule_paths"] == ["/entry"]
    assert strategy_draft.save_blocks(result, row, accept=False) == []


def test_absent_is_refused_when_the_rules_set_the_field_or_it_cannot_be_unset(
    tmp_path: Path,
) -> None:
    row = _ready_card(_store(tmp_path))

    def answer(definition: dict[str, Any], pointer: str) -> Any:
        def call(system: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
            return _reply(
                definition,
                [
                    {"index": 0, "status": "mapped", "rule_paths": ["/entry"], "reason": ""},
                    {"index": 1, "status": "mapped", "rule_paths": ["/exit"], "reason": ""},
                    {"index": 2, "status": "absent", "rule_paths": [pointer], "reason": "x"},
                ],
            )

        return strategy_draft.draft(row, "google/gemini-4-argon", KEYS, call=call)

    set_field = answer({**DONCHIAN, "stop_loss_pct": "5"}, "/stop_loss_pct")
    assert any("the rules set it" in p for p in set_field["blocking"])
    entry = answer(DONCHIAN, "/entry")
    assert any("can be left out" in p for p in entry["blocking"])
    blank = answer(DONCHIAN, "")
    assert any("names no field" in p for p in blank["blocking"])


def test_added_conditions_are_sent_as_the_users_own(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = _ready_card(store)
    row = store.resolve(
        row["card_id"],
        row["version"],
        row["resolutions"]["conditions"],
        {"0": "필수"},
        [{"field": "entry", "text": "50일 평균이 200일 평균보다 위"}],
    )
    items = strategy_draft.card_items(row)
    assert items[2] == "[entry, long, user-added] 50일 평균이 200일 평균보다 위"
    assert items[-1] == "[position] Bet small"
    assert row["ready"]


def test_draft_schema_adds_absent_only_for_technique_drafts() -> None:
    pine = conversion.SCHEMA["properties"]["mapping"]["items"]["properties"]["status"]["enum"]
    draft_enum = strategy_draft.SCHEMA["properties"]["mapping"]["items"]["properties"]["status"]
    assert pine == ["mapped", "unconvertible"]
    assert draft_enum["enum"] == ["mapped", "unconvertible", "absent"]


def test_openrouter_draft_call_returns_the_anthropic_shape_with_cost() -> None:
    seen: list[dict[str, Any]] = []
    message = {
        "choices": [{"finish_reason": "length", "message": {"content": "{}"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.004},
    }
    call, costs = strategy_draft.model_call(
        "google/gemini-4-argon", KEYS, _anthropic_transport(message, seen)
    )
    reply = call("sys", [{"role": "user", "content": "hi"}])
    assert reply["stop_reason"] == "max_tokens"
    assert seen[0]["messages"][0] == {"role": "system", "content": "sys"}
    assert seen[0]["provider"]["data_collection"] == "deny"
    assert costs == [
        {"model": "google/gemini-4-argon", "input_tokens": 10, "output_tokens": 20,
         "cost_usd": "0.004"}
    ]  # fmt: skip


class FakeStrategies:
    def __init__(self) -> None:
        self.saved: list[tuple[str, dict[str, Any]]] = []

    def save_strategy(self, name: str, definition: dict[str, Any]) -> dict[str, Any]:
        self.saved.append((name, definition))
        return {"strategy_id": "strategy-abc", "version": 1}


def test_draft_routes_need_a_ready_card_and_the_users_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)
    service = TechniqueCards(Library(_config(), tmp_path / "cache", FakeCli()), store)
    strategies = FakeStrategies()
    app = FastAPI()

    def error(request: Any, status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status, content={"error": {"code": code, "message": message}}
        )

    register_technique_card_routes(
        app, cards=lambda: service, keys=lambda: KEYS, error_response=error,
        start=lambda w: w(), strategies=lambda: strategies,
    )  # fmt: skip
    client = TestClient(app)
    unready = store.add(
        {
            "item_ref": ITEM, "title": "x", "url": "u", "card": _card(),
            "checks": check_card(_card(), LINES), "usage": {}, "attempts": [], "versions": {},
            "context_ref": "c", "revision_ref": "r", "transcript_sha256": "s",
            "verification_status": "unverified", "recheck_outcome": "conditional",
        }
    )  # fmt: skip
    assert client.post(f"/v1/technique-cards/{unready}/drafts", json={}).status_code == 409

    row = _ready_card(store)
    models: list[str] = []
    real_draft = strategy_draft.draft

    def fake_draft(card: dict[str, Any], model: str, keys: Keys, **choice: Any) -> dict[str, Any]:
        models.append(model)
        return real_draft(
            card, model, keys, **choice,
            call=lambda s, m: _reply(
                DONCHIAN,
                [
                    {"index": 0, "status": "mapped", "rule_paths": ["/entry"], "reason": ""},
                    {"index": 1, "status": "mapped", "rule_paths": ["/exit"], "reason": ""},
                    {"index": 2, "status": "unconvertible", "rule_paths": [], "reason": "size"},
                ],
            ),
        )  # fmt: skip

    monkeypatch.setattr(strategy_draft, "draft", fake_draft)
    started = client.post(
        f"/v1/technique-cards/{row['card_id']}/drafts", json={"model": "google/gemini-4-argon"}
    )
    draft_id = started.json()["draft_id"]
    found = client.get(f"/v1/technique-drafts/{draft_id}").json()
    assert found["state"] == "done" and found["needs_accept"] is True
    assert found["save_blocks"] == [] and models == ["google/gemini-4-argon"]

    refused = client.post(f"/v1/technique-drafts/{draft_id}/save", json={"name": "Seykota"})
    assert refused.status_code == 409 and strategies.saved == []
    saved = client.post(
        f"/v1/technique-drafts/{draft_id}/save",
        json={"name": "Seykota", "accept_unconvertible": True},
    ).json()
    assert saved["strategy_id"] == "strategy-abc" and saved["strategy_version"] == 1
    assert strategies.saved[0][1]["exit"]["right"]["output"] == "lower"
    listed = client.get(f"/v1/technique-cards/{row['card_id']}/drafts").json()["drafts"]
    assert [d["draft_id"] for d in listed] == [draft_id]

    # The user changes the card after the draft: the draft can no longer be saved.
    store.resolve(row["card_id"], row["version"], {**row["resolutions"]["conditions"]}, {})
    stale = client.post(
        f"/v1/technique-drafts/{draft_id}/save",
        json={"name": "Seykota", "accept_unconvertible": True},
    )
    assert stale.status_code == 409 and "changed" in stale.json()["error"]["message"]


def test_default_model_setting_is_used_and_limited_to_fable_argon_and_astra(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _app(tmp_path)
    settings = client.get("/v1/technique-settings").json()
    assert settings["default_model"] == "google/gemini-4-argon"
    assert settings["default_choices"] == [
        "anthropic/claude-fable-5.2", "google/gemini-4-argon", "openai/gpt-6-astra"
    ]  # fmt: skip
    assert settings["default_reset"] is None
    # Paused and back the same day (#LAG-SIW-2, #LAG-SIW-3).
    assert "anthropic/claude-fable-5.2" in settings["models"]
    assert extract.PAUSED_MODELS == ()
    assert (
        client.put("/v1/technique-settings", json={"default_model": "openai/gpt-6-luna"})
        .status_code == 422
    )  # fmt: skip
    fable = client.put(
        "/v1/technique-settings", json={"default_model": "anthropic/claude-fable-5.2"}
    )
    assert fable.json()["default_model"] == "anthropic/claude-fable-5.2"
    changed = client.put("/v1/technique-settings", json={"default_model": "openai/gpt-6-astra"})
    assert changed.json()["default_model"] == "openai/gpt-6-astra"
    used: list[str] = []

    def fake_extract(source_id, title, lines, keys, call=None, transport=None, model="", **_):  # type: ignore[no-untyped-def]
        used.append(model)
        return extract.Extraction(card=_card(), attempts=[{"model": model, "ok": True}])

    monkeypatch.setattr("services.technique_cards.service.extract_card", fake_extract)
    preview = client.post("/v1/technique-sources/preview", json={"item_ref": ITEM}).json()
    assert preview["model"] == "openai/gpt-6-astra"
    assert preview["default_model"] == "openai/gpt-6-astra"
    client.post("/v1/technique-cards", json={"item_ref": ITEM})
    client.post(
        "/v1/technique-cards",
        json={"item_ref": ITEM, "model": "openai/gpt-6-luna", "mode": "new_card"},
    )
    assert used == ["openai/gpt-6-astra", "openai/gpt-6-luna"]
    # The setting survives a new store on the same file.
    assert CardStore(tmp_path / "cards.sqlite3", datetime.now).default_model() == (
        "openai/gpt-6-astra"
    )


def test_a_saved_default_no_longer_offered_goes_back_to_argon_and_says_so(tmp_path: Path) -> None:
    """As when Fable was paused (#LAG-SIW-2): a default no longer offered is put back."""
    client, service = _app(tmp_path)
    service.store.set_default_model("openai/gpt-6-astra")
    with service.store._lock:
        service.store._db.execute(
            "UPDATE technique_settings SET value = 'example/paused-model'"
            " WHERE name = 'default_model'"
        )
    settings = client.get("/v1/technique-settings").json()
    assert settings["default_model"] == "google/gemini-4-argon"
    assert settings["default_reset"]["from"] == "example/paused-model"
    assert settings["default_reset"]["to"] == "google/gemini-4-argon"
    reopened = CardStore(tmp_path / "cards.sqlite3", datetime.now)
    assert reopened.default_model() == "google/gemini-4-argon"
    assert reopened.default_model_reset() == settings["default_reset"]


def test_fable_and_astra_go_through_openrouter() -> None:
    models: list[str] = []
    for model in ("anthropic/claude-fable-5.2", "openai/gpt-6-astra"):
        seen: list[dict[str, Any]] = []
        message = {
            "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(_card())}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0.001},
        }
        extract._default_caller(_anthropic_transport(message, seen))(model, "s", "p", KEYS)
        models.append(seen[0]["model"])
        assert seen[0]["provider"]["data_collection"] == "deny"
    assert models == ["anthropic/claude-fable-5.2", "openai/gpt-6-astra"]


# --- one source, several cards (ADR 0056, 2026-11-23) ------------------------------------


def _fake_extract_with(card: dict[str, Any]):  # type: ignore[no-untyped-def]
    def fake(source_id, title, lines, keys, call=None, transport=None, model="", **_):  # type: ignore[no-untyped-def]
        return extract.Extraction(
            card=copy.deepcopy(card),
            attempts=[{"part": 1, "model": "google/gemini-4-argon", "ok": True}],
        )

    return fake


def test_a_second_extraction_asks_new_version_or_new_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _app(tmp_path)
    monkeypatch.setattr(
        "services.technique_cards.service.extract_card", _fake_extract_with(_card())
    )
    first = client.post("/v1/technique-cards", json={"item_ref": ITEM}).json()
    card_id = client.get(f"/v1/technique-cards/jobs/{first['job_id']}").json()["card_id"]
    saved = client.post(
        f"/v1/technique-cards/{card_id}/resolutions",
        json={"version": 1, "conditions": {"3": {"action": "drop"}}, "ambiguities": {"0": "2배"}},
    )
    assert saved.status_code == 200

    asked = client.post("/v1/technique-cards", json={"item_ref": ITEM})
    assert asked.status_code == 409
    assert asked.json()["error"]["code"] == "technique_card_exists"
    assert card_id in asked.json()["error"]["message"]
    wrong = client.post(
        "/v1/technique-cards", json={"item_ref": ITEM, "mode": "new_version", "card_id": "x"}
    )
    assert wrong.status_code == 422

    second = copy.deepcopy(_card())
    second["conditions"] = second["conditions"][:2]
    monkeypatch.setattr("services.technique_cards.service.extract_card", _fake_extract_with(second))
    again = client.post(
        "/v1/technique-cards", json={"item_ref": ITEM, "mode": "new_version", "card_id": card_id}
    ).json()
    job = client.get(f"/v1/technique-cards/jobs/{again['job_id']}").json()
    assert job["state"] == "done" and job["card_id"] == card_id
    card = client.get(f"/v1/technique-cards/{card_id}").json()
    assert card["version"] == 3 and len(card["card"]["conditions"]) == 2
    assert card["resolutions"] == {"conditions": {}, "ambiguities": {}}
    kept = service.store.extractions(card_id)
    assert [k["version"] for k in kept] == [2]
    assert len(kept[0]["card"]["conditions"]) == len(_card()["conditions"])
    assert kept[0]["resolutions"]["ambiguities"] == {"0": "2배"}
    assert len(client.get("/v1/technique-cards").json()["cards"]) == 1

    other = client.post("/v1/technique-cards", json={"item_ref": ITEM, "mode": "new_card"}).json()
    other_id = client.get(f"/v1/technique-cards/jobs/{other['job_id']}").json()["card_id"]
    assert other_id != card_id
    assert len(client.get("/v1/technique-cards").json()["cards"]) == 2


def test_the_user_withdraws_one_card_and_it_is_kept_but_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, service = _app(tmp_path)
    monkeypatch.setattr(
        "services.technique_cards.service.extract_card", _fake_extract_with(_card())
    )
    job = client.post("/v1/technique-cards", json={"item_ref": ITEM}).json()
    card_id = client.get(f"/v1/technique-cards/jobs/{job['job_id']}").json()["card_id"]
    client.post(
        f"/v1/technique-cards/{card_id}/resolutions",
        json={"version": 1, "conditions": {"1": {"action": "confirm"}, "3": {"action": "drop"}}},
    )
    assert client.get(f"/v1/technique-cards/{card_id}").json()["ready"] is True

    assert client.post("/v1/technique-cards/nope/withdraw", json={}).status_code == 404
    out = client.post(f"/v1/technique-cards/{card_id}/withdraw", json={"note": " 중복 "}).json()
    assert out["user_withdrawn_at"] == "2026-11-14T00:00:00Z"
    assert out["user_withdrawn_note"] == "중복"
    assert out["ready"] is False and "withdrawn by the user" in out["blocks"]
    listed = client.get("/v1/technique-cards").json()["cards"]
    assert listed[0]["user_withdrawn_at"]  # still listed, marked
    assert client.get("/v1/technique-sources").json()["sources"][0]["card_ids"] == []
    # With the only card withdrawn, a new extraction is a new card without asking.
    assert client.post("/v1/technique-cards", json={"item_ref": ITEM}).status_code == 202
    refused = client.post(
        "/v1/technique-cards", json={"item_ref": ITEM, "mode": "new_version", "card_id": card_id}
    )
    assert refused.status_code == 422
    assert service.store.get(card_id)["version"] == 2


def test_a_saved_edit_can_be_sent_back_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _service = _app(tmp_path)
    monkeypatch.setattr(
        "services.technique_cards.service.extract_card", _fake_extract_with(_card())
    )
    job = client.post("/v1/technique-cards", json={"item_ref": ITEM}).json()
    card_id = client.get(f"/v1/technique-cards/jobs/{job['job_id']}").json()["card_id"]
    edited = copy.deepcopy(_card()["conditions"][1]) | {"left": _op("indicator", "rsi", period=2)}
    client.post(
        f"/v1/technique-cards/{card_id}/resolutions",
        json={"version": 1, "conditions": {"1": {"action": "edit", "condition": edited}}},
    )
    stored = client.get(f"/v1/technique-cards/{card_id}").json()["resolutions"]["conditions"]
    assert stored["1"]["condition"]["basis"] == "user"
    again = client.post(
        f"/v1/technique-cards/{card_id}/resolutions",
        json={"version": 2, "conditions": stored, "ambiguities": {"0": "2배"}},
    )
    assert again.status_code == 200, again.json()
