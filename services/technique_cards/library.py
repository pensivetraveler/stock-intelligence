"""Technique sources from the knowledge library (ADR 0056 L1).

The library is reached only through its CLI (`python -I -m kl.cli`, #SIW-lib-1): the SIW API
process runs under the same OS account, so the library binds it as the user's session. Every
answer is one JSON envelope; exit code 0 is not "allowed", the envelope is judged here.

- The list is the tag subscription's deliveries (topic `매매기법`, paged to the end).
- A source's text is fetched with `context assemble` + `context hydrate` into a cache outside
  the repository (directories 0700, files 0600). The layer kept is, in this order: the hydrated
  file with the most `[mm:ss]` lines (captions); else the one with the most `[p. N]` lines (a
  PDF's text, knowledge-library pdftext); else the longest text the library records as observed
  and not a summary or interpretation (`archive get`: a note the investor wrote, a web page's
  body), never JSON metadata or the PDF's own bytes (user request 2026-11-15).
- Before each reuse the cached text is rechecked (`context hydration-check`): `still_usable`
  is used, `conditional` is used and shown as conditional, `stop_using` / `unknown` deletes the
  cache and the caller marks the card's evidence withdrawn.
- Third-party text never goes into the repository, logs or error messages.
- A series (volumes 1 and 2 of one book, `item.series = {"name", "index"}`, #SIW-lib-23) is one
  source: the list shows it once (`kl-series:<name>`), and its text is the volumes in order with
  pages (or paragraphs, or times) numbered on from the volume before (user request 2026-11-22).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TAG = "매매기법"
USABLE = ("still_usable", "conditional")
WITHDRAWN = ("stop_using", "unknown")
TIMED_LINE = re.compile(r"^\[\d+:\d{2}(?::\d{2})?\]", re.MULTILINE)
PAGE_LINE = re.compile(r"^\[p\.\s?\d+\]\s*$", re.MULTILINE)
# Layers a summary or a model's reading, never the source itself.
DERIVED_KINDS = ("summary", "interpretation")
TIMEOUT_SECONDS = 120
# Each library call is its own process (~1-2 s); item lookups for the list run side by side.
LIST_WORKERS = 6
SERIES_PREFIX = "kl-series:"

Runner = Callable[[list[str], dict[str, Any]], dict[str, Any]]


def _is_json(text: str) -> bool:
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


class LibraryError(RuntimeError):
    """The library refused or could not answer; the message never carries source text."""

    def __init__(self, message: str, reason_code: str = "") -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LibraryConfig:
    root: Path
    task_ref: str
    tagsubscription_ref: str
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> LibraryConfig | None:
        """None unless SIW_KL_ROOT, SIW_KL_TASK and SIW_KL_TAGSUBSCRIPTION are all set."""
        source = os.environ if environ is None else environ
        root = source.get("SIW_KL_ROOT", "").strip()
        task = source.get("SIW_KL_TASK", "").strip()
        subscription = source.get("SIW_KL_TAGSUBSCRIPTION", "").strip()
        if not (root and task and subscription):
            return None
        instance = source.get("SIW_KL_INSTANCE", "kl-lib").strip() or "kl-lib"
        share = Path(source.get("HOME", str(Path.home()))) / ".local/share/knowledge-library"
        env = {
            "KL_INSTANCE": instance,
            "KL_PG_DB": source.get("KL_PG_DB", instance.replace("-", "_")),
            "KL_BLOB_DIR": source.get("KL_BLOB_DIR", str(share / instance / "blobs")),
            "KL_FINGERPRINT_KEYS": source.get(
                "KL_FINGERPRINT_KEYS", str(share / instance / "fingerprint-keys.json")
            ),
        }
        return cls(Path(root).expanduser(), task, subscription, env)


def cli_runner(config: LibraryConfig) -> Runner:
    """Runs `kl.cli <args> --input -` with the request on stdin and returns the envelope."""

    def run(args: list[str], request: dict[str, Any]) -> dict[str, Any]:
        python = config.root / ".venv/bin/python"
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": os.environ.get("HOME", ""),
            **config.env,
        }
        try:
            proc = subprocess.run(
                [str(python), "-I", "-m", "kl.cli", *args, "--input", "-"],
                input=json.dumps(request, ensure_ascii=False),
                capture_output=True,
                text=True,
                env=env,
                cwd=config.root,
                timeout=TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise LibraryError(f"could not run the library CLI: {type(exc).__name__}") from exc
        try:
            envelope = json.loads(proc.stdout)
        except ValueError as exc:
            raise LibraryError(f"library CLI exit {proc.returncode}, no envelope") from exc
        if not isinstance(envelope, dict):
            raise LibraryError("library CLI answered with something other than an envelope")
        return envelope

    return run


@dataclass(frozen=True)
class Source:
    item_ref: str
    title: str
    url: str
    source_key: str
    tags: list[str]
    delivered_at: str
    eligibility: list[str]
    series: str = ""
    series_index: int = 0
    # A series source: its volumes, in order (each a single-item source).
    members: tuple[Source, ...] = ()

    @property
    def is_series(self) -> bool:
        return bool(self.members)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "item_ref": self.item_ref,
            "title": self.title,
            "url": self.url,
            "source_key": self.source_key,
            "tags": self.tags,
            "delivered_at": self.delivered_at,
            "eligibility": self.eligibility,
        }
        if self.members:
            out["series"] = {
                "name": self.series,
                "volumes": [
                    {"item_ref": m.item_ref, "title": m.title, "index": m.series_index}
                    for m in self.members
                ],
            }
        return out


def series_ref(name: str) -> str:
    return SERIES_PREFIX + name


def group_series(sources: list[Source]) -> list[Source]:
    """Volumes of one series become one source; a series with one volume here stays as it is."""
    by_name: dict[str, list[Source]] = {}
    for source in sources:
        if source.series:
            by_name.setdefault(source.series.casefold(), []).append(source)
    out: list[Source] = []
    placed: set[str] = set()
    for source in sources:
        key = source.series.casefold()
        volumes = by_name.get(key, []) if source.series else []
        if len(volumes) < 2:
            out.append(source)
            continue
        if key in placed:
            continue
        placed.add(key)
        volumes = sorted(volumes, key=lambda v: (v.series_index, v.title))
        out.append(
            Source(
                item_ref=series_ref(volumes[0].series),
                title=volumes[0].series,
                url="",
                source_key=series_ref(volumes[0].series)[:100],
                tags=sorted({t for v in volumes for t in v.tags}),
                delivered_at=max(v.delivered_at for v in volumes),
                eligibility=[e for v in volumes for e in v.eligibility],
                series=volumes[0].series,
                members=tuple(volumes),
            )
        )
    return out


@dataclass(frozen=True)
class Fetched:
    """A cached transcript: where it is and what to recheck before using it again."""

    item_ref: str
    context_ref: str
    revision_ref: str
    path: Path
    sha256: str
    verification_status: str
    data_grade: str
    # A series: every volume's {item_ref, context_ref, revision_ref}; context_ref and
    # revision_ref above are the first volume's.
    members: tuple[dict[str, str], ...] = ()


class Library:
    def __init__(self, config: LibraryConfig, cache_dir: Path, run: Runner | None = None) -> None:
        self.config = config
        self.cache_dir = cache_dir
        self._run = run or cli_runner(config)
        self._locks: dict[str, threading.RLock] = {}
        self._locks_guard = threading.Lock()

    def lock(self, item_ref: str) -> threading.RLock:
        """One lock per item: two requests never fetch or replace the same cache folder at once."""
        with self._locks_guard:
            return self._locks.setdefault(item_ref, threading.RLock())

    def _call(self, args: list[str], request: dict[str, Any]) -> dict[str, Any]:
        envelope = self._run(args, request)
        status = envelope.get("status")
        if status not in ("success", "partial_success"):
            code = str(envelope.get("reason_code") or "")
            raise LibraryError(f"library refused {' '.join(args[:2])}: {code}", code)
        data = envelope.get("data")
        if not isinstance(data, dict):
            raise LibraryError(f"library answered {' '.join(args[:2])} without data")
        return data

    def sources(self) -> list[Source]:
        """Every item the tag subscription delivered, newest first, paged to the end."""
        deliveries: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(100):  # a runaway cursor stops; 100 pages is far beyond today's 18 items
            args = ["delivery", "list"] + (["--cursor", cursor] if cursor else [])
            data = self._call(args, {"tagsubscription_ref": self.config.tagsubscription_ref})
            deliveries.extend(d for d in data.get("deliveries") or [] if isinstance(d, dict))
            cursor = data.get("next_cursor")
            if not cursor:
                break
        else:
            raise LibraryError("delivery list did not end")
        latest: dict[str, dict[str, Any]] = {}
        for delivery in deliveries:
            ref = str(delivery.get("item_ref") or "")
            newer = str(delivery.get("sent_at", "")) >= str(latest.get(ref, {}).get("sent_at", ""))
            if ref and delivery.get("state") == "sent" and newer:
                latest[ref] = delivery
        with ThreadPoolExecutor(max_workers=LIST_WORKERS) as pool:
            looked_up = pool.map(
                lambda pair: self.source(pair[0], str(pair[1].get("sent_at") or "")),
                list(latest.items()),
            )
            found = [source for source in looked_up if source is not None]
        found.sort(key=lambda s: s.delivered_at, reverse=True)
        return group_series(found)

    def source(self, item_ref: str, delivered_at: str = "") -> Source | None:
        """One current item carrying the technique tag, or None."""
        data = self._call(["item", "get"], {"item_ref": item_ref})
        item = data.get("item") or {}
        tags = sorted({str(t.get("name")) for t in item.get("tags") or [] if isinstance(t, dict)})
        if item.get("state") not in (None, "current") or TAG not in tags:
            return None
        raw_series = item.get("series")
        series: dict[str, Any] = raw_series if isinstance(raw_series, dict) else {}
        name = str(series.get("name") or "").strip()[:256]  # contract 0.5.1: 1-256 characters
        try:
            index = int(series.get("index") or 0)
        except (TypeError, ValueError):
            index = 0
        return Source(
            item_ref=item_ref,
            title=str(item.get("title") or "")[:500],
            url=str(item.get("url") or "")[:500],
            source_key=str(item.get("source_key") or "")[:100],
            tags=tags,
            delivered_at=delivered_at,
            eligibility=[str(m.get("eligibility_outcome")) for m in data.get("members") or []],
            series=name,
            series_index=index if name else 0,
        )

    def _item_dir(self, item_ref: str) -> Path:
        return self.cache_dir / hashlib.sha256(item_ref.encode()).hexdigest()[:32]

    def fetch(self, item_ref: str) -> Fetched:
        """Hydrate the item's layers into a fresh cache folder and keep the transcript layer.

        The layers are hydrated into a private staging folder and moved into place only when
        complete, under the item's lock: a second request for the same item waits instead of
        deleting the folder the first one is still writing (2026-11-22, a book cached its
        metadata note instead of its PDF text).
        """
        with self.lock(item_ref):
            return self._fetch(item_ref)

    def _fetch(self, item_ref: str) -> Fetched:
        data = self._call(["item", "get"], {"item_ref": item_ref})
        refs = [str(m["artifact_ref"]) for m in data.get("members") or [] if m.get("artifact_ref")]
        if not refs:
            raise LibraryError("the item has no layers")
        context = self._call(
            ["context", "assemble"],
            {"task_ref": self.config.task_ref, "candidate_artifact_refs": refs},
        )
        selected = [
            {"revision_ref": i["revision_ref"], "anchor_refs": []}
            for i in context.get("selected_items") or []
            if i.get("revision_ref")
        ]
        if not selected:
            raise LibraryError("the library selected no layer of this item")
        target = self._item_dir(item_ref)
        self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.cache_dir, 0o700)
        staging = Path(tempfile.mkdtemp(prefix=".fetch-", dir=self.cache_dir))  # 0700
        try:
            hydrated = staging / "hydrated"
            hydrated.mkdir(mode=0o700)
            self._call(
                ["context", "hydrate", "--out-dir", str(hydrated)],
                {"context_ref": context["context_ref"], "items": selected},
            )
            path, meta = self._source_layer(hydrated)
            shutil.copyfile(path, staging / "transcript.txt")
            os.chmod(staging / "transcript.txt", 0o600)
            shutil.rmtree(hydrated)
            if target.exists():
                shutil.rmtree(target)
            os.rename(staging, target)
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        transcript = target / "transcript.txt"
        fetched = Fetched(
            item_ref=item_ref,
            context_ref=str(context["context_ref"]),
            revision_ref=str(meta["revision_ref"]),
            path=transcript,
            sha256=hashlib.sha256(transcript.read_bytes()).hexdigest(),
            verification_status=str(meta.get("verification_status") or "unverified"),
            data_grade=str(meta.get("data_grade") or "private"),
        )
        record = target / "fetched.json"
        record.write_text(
            json.dumps(
                {
                    "item_ref": item_ref,
                    "context_ref": fetched.context_ref,
                    "revision_ref": fetched.revision_ref,
                    "sha256": fetched.sha256,
                    "verification_status": fetched.verification_status,
                    "data_grade": fetched.data_grade,
                }
            )
        )
        os.chmod(record, 0o600)
        return fetched

    def _source_layer(self, hydrated: Path) -> tuple[Path, dict[str, Any]]:
        """The hydrated file to use as the source, and its meta (see the module note)."""
        files: list[tuple[Path, str, dict[str, Any]]] = []
        for path in sorted(hydrated.iterdir()):
            if path.name.endswith(".meta.json"):
                continue
            meta_path = path.with_name(path.name + ".meta.json")
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            if not meta.get("revision_ref"):
                continue
            data = path.read_bytes()
            if b"\x00" in data or data.startswith(b"%PDF"):
                continue  # binary: the PDF itself
            files.append((path, data.decode("utf-8", errors="replace"), meta))
        for pattern in (TIMED_LINE, PAGE_LINE):
            counted = [(len(pattern.findall(text)), path, meta) for path, text, meta in files]
            counted = [c for c in counted if c[0]]
            if counted:
                _count, path, meta = max(counted, key=lambda c: c[0])
                return path, meta
        written: list[tuple[int, Path, dict[str, Any]]] = []
        for path, text, meta in files:
            if not text.strip() or _is_json(text):
                continue
            layer = self._call(
                ["archive", "get"], {"target": {"revision_ref": meta["revision_ref"]}}
            )
            if layer.get("origin_type") == "observed" and layer.get("kind") not in DERIVED_KINDS:
                written.append((len(text.strip()), path, meta))
        if written:
            _size, path, meta = max(written, key=lambda w: w[0])
            return path, meta
        raise LibraryError("no captions, PDF text or written text in this item")

    def cached(self, item_ref: str) -> Fetched | None:
        """The cached transcript of an item, if it is there and unchanged."""
        target = self._item_dir(item_ref)
        try:
            record = json.loads((target / "fetched.json").read_text())
            transcript = target / "transcript.txt"
            digest = hashlib.sha256(transcript.read_bytes()).hexdigest()
        except (OSError, ValueError):
            return None
        if record.get("item_ref") != item_ref or record.get("sha256") != digest:
            return None
        return Fetched(
            item_ref=item_ref,
            context_ref=str(record["context_ref"]),
            revision_ref=str(record["revision_ref"]),
            path=transcript,
            sha256=digest,
            verification_status=str(record.get("verification_status") or "unverified"),
            data_grade=str(record.get("data_grade") or "private"),
            members=tuple(record.get("members") or ()),
        )

    def keep_series(self, series_item_ref: str, text: str, volumes: list[Fetched]) -> Fetched:
        """Cache a series' joined text like an item's, recording every volume to recheck."""
        with self.lock(series_item_ref):
            target = self._item_dir(series_item_ref)
            self.cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(self.cache_dir, 0o700)
            staging = Path(tempfile.mkdtemp(prefix=".fetch-", dir=self.cache_dir))
            members = tuple(
                {
                    "item_ref": v.item_ref,
                    "context_ref": v.context_ref,
                    "revision_ref": v.revision_ref,
                }
                for v in volumes
            )
            grades = {v.data_grade for v in volumes}
            statuses = {v.verification_status for v in volumes}
            first = volumes[0]
            try:
                transcript = staging / "transcript.txt"
                transcript.write_text(text, encoding="utf-8")
                os.chmod(transcript, 0o600)
                fetched = Fetched(
                    item_ref=series_item_ref,
                    context_ref=first.context_ref,
                    revision_ref=first.revision_ref,
                    path=target / "transcript.txt",
                    sha256=hashlib.sha256(transcript.read_bytes()).hexdigest(),
                    # Volumes that differ: the series is unverified and private.
                    verification_status=first.verification_status
                    if len(statuses) == 1
                    else "unverified",
                    data_grade=first.data_grade if len(grades) == 1 else "private",
                    members=members,
                )
                record = staging / "fetched.json"
                record.write_text(
                    json.dumps(
                        {
                            "item_ref": series_item_ref,
                            "context_ref": fetched.context_ref,
                            "revision_ref": fetched.revision_ref,
                            "sha256": fetched.sha256,
                            "verification_status": fetched.verification_status,
                            "data_grade": fetched.data_grade,
                            "members": list(members),
                        }
                    )
                )
                os.chmod(record, 0o600)
                if target.exists():
                    shutil.rmtree(target)
                os.rename(staging, target)
            except BaseException:
                shutil.rmtree(staging, ignore_errors=True)
                raise
            return fetched

    def recheck(self, context_ref: str, revision_ref: str) -> str:
        """`still_usable` | `conditional` | `stop_using` | `unknown` (refused = unknown)."""
        try:
            data = self._call(
                ["context", "hydration-check"],
                {"context_ref": context_ref, "revision_ref": revision_ref, "anchor_refs": []},
            )
        except LibraryError as exc:
            if exc.reason_code in ("NOT_AVAILABLE", "NOT_FOUND", "NOT_ELIGIBLE"):
                return "unknown"
            raise
        outcome = str(data.get("outcome") or "unknown")
        return outcome if outcome in USABLE + WITHDRAWN else "unknown"

    def forget(self, item_ref: str) -> None:
        """Delete the cached text of an item (evidence withdrawn)."""
        with self.lock(item_ref):
            target = self._item_dir(item_ref)
            if target.exists():
                shutil.rmtree(target)
