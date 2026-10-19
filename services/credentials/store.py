"""Registered provider keys: labels in SQLite, secret values in the OS keychain.

Secrets are write-only from the caller's point of view: they can be stored, replaced and
deleted, and are read back only to build an adapter. No method returns them for display,
and error messages never include them.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
from uuid import uuid4

from services.credentials.catalog import CATALOG
from services.market_data.storage import MigrationRunner

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
KEYCHAIN_SERVICE = "stock-intelligence-workstation"
ACCOUNT_SUFFIX_PATTERN = re.compile(r"^[0-9]{4}$")
MAX_LABEL_LENGTH = 60
MAX_SECRET_LENGTH = 1_024


class CredentialError(ValueError):
    """A request that cannot become a valid registered key."""


class CredentialNotFoundError(CredentialError):
    """No registered key has this ID."""


class SecretStorageError(RuntimeError):
    """The OS keychain could not store, read or delete a secret."""


class SecretBackend(Protocol):
    def get(self, name: str) -> str | None: ...

    def set(self, name: str, value: str) -> None: ...

    def delete(self, name: str) -> None: ...


class KeyringSecretBackend:
    """macOS Keychain / Windows Credential Manager / Secret Service through `keyring`."""

    def __init__(self, service: str = KEYCHAIN_SERVICE) -> None:
        self.service = service

    def get(self, name: str) -> str | None:
        import keyring
        from keyring.errors import KeyringError

        try:
            return keyring.get_password(self.service, name)
        except KeyringError as exc:
            raise SecretStorageError(
                f"the OS keychain could not be read: {type(exc).__name__}"
            ) from exc

    def set(self, name: str, value: str) -> None:
        import keyring
        from keyring.errors import KeyringError

        try:
            keyring.set_password(self.service, name, value)
        except KeyringError as exc:
            raise SecretStorageError(
                f"the OS keychain refused to store the key: {type(exc).__name__}"
            ) from exc

    def delete(self, name: str) -> None:
        import keyring
        from keyring.errors import KeyringError, PasswordDeleteError

        try:
            keyring.delete_password(self.service, name)
        except PasswordDeleteError:
            return  # already gone
        except KeyringError as exc:
            raise SecretStorageError(
                f"the OS keychain could not delete the key: {type(exc).__name__}"
            ) from exc


class MemorySecretBackend:
    """In-process secret storage for tests."""

    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.items.get(name)

    def set(self, name: str, value: str) -> None:
        self.items[name] = value

    def delete(self, name: str) -> None:
        self.items.pop(name, None)


@dataclass(frozen=True)
class CredentialRecord:
    credential_id: str
    provider: str
    label: str
    account_suffix: str | None
    priority: int
    enabled: bool
    secret_revision: int
    created_at_utc: str
    updated_at_utc: str

    @property
    def keychain_name(self) -> str:
        return f"{self.provider}:{self.credential_id}"


def _utc_text(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _record(row: sqlite3.Row) -> CredentialRecord:
    return CredentialRecord(
        credential_id=row["credential_id"],
        provider=row["provider"],
        label=row["label"],
        account_suffix=row["account_suffix"],
        priority=row["priority"],
        enabled=bool(row["enabled"]),
        secret_revision=row["secret_revision"],
        created_at_utc=row["created_at_utc"],
        updated_at_utc=row["updated_at_utc"],
    )


_UNSET: Any = object()


class CredentialStore:
    def __init__(
        self,
        root: Path,
        secrets: SecretBackend,
        *,
        now: Callable[[], datetime] | None = None,
        new_id: Callable[[], str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database_path = self.root / "credentials.sqlite3"
        self._secrets = secrets
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._new_id = new_id or (lambda: uuid4().hex)
        MigrationRunner(self.database_path, MIGRATIONS_DIR).apply()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            with connection:
                yield connection

    def list(self, provider: str | None = None) -> list[CredentialRecord]:
        with self._connect() as connection:
            if provider is None:
                rows = connection.execute(
                    "SELECT * FROM credentials ORDER BY provider, priority"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM credentials WHERE provider = ? ORDER BY priority", (provider,)
                ).fetchall()
        return [_record(row) for row in rows]

    def get(self, credential_id: str) -> CredentialRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM credentials WHERE credential_id = ?", (credential_id,)
            ).fetchone()
        if row is None:
            raise CredentialNotFoundError("no registered key has this ID")
        return _record(row)

    def add(
        self,
        provider: str,
        label: str,
        account_suffix: str | None,
        secrets: Mapping[str, str],
    ) -> CredentialRecord:
        if provider not in CATALOG:
            raise CredentialError(f"unknown provider {provider!r}")
        label = self._label(label)
        account_suffix = self._account_suffix(provider, account_suffix)
        blob = self._secret_blob(provider, secrets)
        credential_id = self._new_id()
        now = _utc_text(self._now())
        keychain_name = f"{provider}:{credential_id}"
        # Keychain first: a row never points at a secret that was not stored.
        self._secrets.set(keychain_name, blob)
        try:
            with self._connect() as connection:
                self._check_label_free(connection, provider, label, None)
                (top,) = connection.execute(
                    "SELECT COALESCE(MAX(priority), 0) FROM credentials WHERE provider = ?",
                    (provider,),
                ).fetchone()
                connection.execute(
                    "INSERT INTO credentials VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?)",
                    (credential_id, provider, label, account_suffix, top + 1, now, now),
                )
        except BaseException:
            self._secrets.delete(keychain_name)
            raise
        return self.get(credential_id)

    def update(
        self,
        credential_id: str,
        *,
        label: str | None = None,
        account_suffix: str | None = _UNSET,
        enabled: bool | None = None,
    ) -> CredentialRecord:
        current = self.get(credential_id)
        new_label = current.label if label is None else self._label(label)
        new_suffix = (
            current.account_suffix
            if account_suffix is _UNSET
            else self._account_suffix(current.provider, account_suffix)
        )
        new_enabled = current.enabled if enabled is None else enabled
        with self._connect() as connection:
            self._check_label_free(connection, current.provider, new_label, credential_id)
            connection.execute(
                "UPDATE credentials SET label = ?, account_suffix = ?, enabled = ?, "
                "updated_at_utc = ? WHERE credential_id = ?",
                (
                    new_label,
                    new_suffix,
                    int(new_enabled),
                    _utc_text(self._now()),
                    credential_id,
                ),
            )
        return self.get(credential_id)

    def move(self, credential_id: str, direction: Literal["up", "down"]) -> CredentialRecord:
        """Swap places with the neighbouring key of the same provider."""
        current = self.get(credential_id)
        siblings = self.list(current.provider)
        index = next(i for i, item in enumerate(siblings) if item.credential_id == credential_id)
        other_index = index - 1 if direction == "up" else index + 1
        if 0 <= other_index < len(siblings):
            other = siblings[other_index]
            with self._connect() as connection:
                for item, priority in ((current, other.priority), (other, current.priority)):
                    connection.execute(
                        "UPDATE credentials SET priority = ? WHERE credential_id = ?",
                        (priority, item.credential_id),
                    )
        return self.get(credential_id)

    def replace_secrets(self, credential_id: str, secrets: Mapping[str, str]) -> CredentialRecord:
        current = self.get(credential_id)
        self._secrets.set(current.keychain_name, self._secret_blob(current.provider, secrets))
        with self._connect() as connection:
            connection.execute(
                "UPDATE credentials SET secret_revision = secret_revision + 1, "
                "updated_at_utc = ? WHERE credential_id = ?",
                (_utc_text(self._now()), credential_id),
            )
        return self.get(credential_id)

    def delete(self, credential_id: str) -> None:
        current = self.get(credential_id)
        with self._connect() as connection:
            connection.execute("DELETE FROM credentials WHERE credential_id = ?", (credential_id,))
        self._secrets.delete(current.keychain_name)

    def secrets(self, record: CredentialRecord) -> dict[str, str]:
        """Secret values of one key, for building its adapter only."""
        blob = self._secrets.get(record.keychain_name)
        if blob is None:
            raise SecretStorageError(
                f"the keychain has no secret for key {record.label!r}; enter it again"
            )
        values = json.loads(blob)
        expected = {field.name for field in CATALOG[record.provider].fields}
        if not isinstance(values, dict) or set(values) != expected:
            raise SecretStorageError(
                f"the keychain entry for key {record.label!r} is incomplete; enter it again"
            )
        return {str(name): str(value) for name, value in values.items()}

    @staticmethod
    def _label(label: str) -> str:
        cleaned = label.strip()
        if not cleaned or len(cleaned) > MAX_LABEL_LENGTH:
            raise CredentialError(f"label must be 1-{MAX_LABEL_LENGTH} characters")
        return cleaned

    @staticmethod
    def _account_suffix(provider: str, account_suffix: str | None) -> str | None:
        if account_suffix is None or account_suffix == "":
            return None
        if not CATALOG[provider].per_account:
            raise CredentialError(f"{CATALOG[provider].display_name} keys are not per account")
        if not ACCOUNT_SUFFIX_PATTERN.fullmatch(account_suffix):
            raise CredentialError("account suffix is the last 4 digits only")
        return account_suffix

    @staticmethod
    def _secret_blob(provider: str, secrets: Mapping[str, str]) -> str:
        expected = [field.name for field in CATALOG[provider].fields]
        if set(secrets) != set(expected):
            raise CredentialError(f"enter {', '.join(expected)}")
        cleaned: dict[str, str] = {}
        for name in expected:
            value = secrets[name].strip()
            if not value or len(value) > MAX_SECRET_LENGTH:
                raise CredentialError(f"{name} must be 1-{MAX_SECRET_LENGTH} characters")
            cleaned[name] = value
        return json.dumps(cleaned, separators=(",", ":"))

    @staticmethod
    def _check_label_free(
        connection: sqlite3.Connection, provider: str, label: str, credential_id: str | None
    ) -> None:
        row = connection.execute(
            "SELECT credential_id FROM credentials WHERE provider = ? AND label = ?",
            (provider, label),
        ).fetchone()
        if row is not None and row["credential_id"] != credential_id:
            raise CredentialError(
                f"a {CATALOG[provider].display_name} key is already named {label!r}"
            )
