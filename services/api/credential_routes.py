"""Routes for registering provider keys (Keys screen).

Secrets travel one way: in through POST/PUT bodies, straight to the OS keychain. No
response, log line or error message contains them. Because these routes accept secrets
and change which keys the app uses, they also refuse requests that did not come from the
app itself: a Host other than the loopback address (DNS rebinding) or an Origin outside
the CORS allowlist (another web page in the user's browser).
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, SecretStr, StringConstraints
from starlette.responses import Response

from packages.contracts.validator import validate_document
from services.credentials.catalog import CATALOG
from services.credentials.store import (
    CredentialError,
    CredentialNotFoundError,
    CredentialRecord,
    CredentialStore,
    SecretStorageError,
)

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]"})
Provider = Literal["alpaca", "kis", "toss", "kiwoom", "meritz", "finra", "ecos", "anthropic"]
Label = Annotated[str, StringConstraints(min_length=1, max_length=60)]
AccountSuffix = Annotated[str, StringConstraints(pattern=r"^[0-9]{4}$")]
Environment = Literal["real", "demo"]
CredentialId = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]
SecretValues = dict[Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,40}$")], SecretStr]
ErrorResponse = Callable[[Request, int, str, str], JSONResponse]
NO_STORE = {"Cache-Control": "no-store"}


class CredentialCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Provider
    label: Label
    account_suffix: AccountSuffix | None = None
    # Required for broker keys (per account), absent for vendor keys.
    environment: Environment | None = None
    secrets: SecretValues


class CredentialUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: Label | None = None
    account_suffix: AccountSuffix | None = None
    enabled: bool | None = None
    environment: Environment | None = None


class SecretsReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    secrets: SecretValues


class MoveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    direction: Literal["up", "down"]


def _plain(secrets: SecretValues) -> dict[str, str]:
    return {name: value.get_secret_value() for name, value in secrets.items()}


def register_credential_routes(
    application: FastAPI,
    *,
    credentials: Callable[[], CredentialStore | None],
    active_provider: Callable[[], str],
    key_status: Callable[[], dict[str, dict[str, Any]]],
    allowed_origins: Collection[str],
    error_response: ErrorResponse,
    allowed_hosts: Collection[str] = LOCAL_HOSTS,
) -> None:
    def refused(request: Request) -> JSONResponse | None:
        host = request.headers.get("host", "")
        hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        if hostname not in allowed_hosts:
            return error_response(
                request, 403, "credential_request_refused", "Keys can be managed only locally"
            )
        origin = request.headers.get("origin")
        if origin is not None and origin not in allowed_origins:
            return error_response(
                request, 403, "credential_request_refused", "Keys can be managed only from the app"
            )
        return None

    def unavailable(request: Request) -> JSONResponse:
        return error_response(
            request,
            503,
            "credential_storage_unconfigured",
            "Set SIW_DATA_DIR to register keys; nothing was stored",
        )

    def summary(record: CredentialRecord, active: str, status: dict[str, Any]) -> dict[str, Any]:
        is_active = record.provider == active
        document = {
            "kind": "credential_summary",
            "schema_version": "0.1",
            "credential_id": record.credential_id,
            "provider": record.provider,
            "label": record.label,
            "account_suffix": record.account_suffix,
            "environment": record.environment,
            "priority": record.priority,
            "enabled": record.enabled,
            "active": is_active,
            "created_at_utc": record.created_at_utc,
            "updated_at_utc": record.updated_at_utc,
            "status": (
                status.get(
                    record.credential_id,
                    {
                        "state": "idle",
                        "cooling_until_utc": None,
                        "last_error": None,
                        "last_used_at_utc": None,
                    },
                )
                if is_active
                else None
            ),
        }
        validate_document(document)
        return document

    def respond(record: CredentialRecord, status_code: int = 200) -> JSONResponse:
        return JSONResponse(
            status_code=status_code,
            content=summary(record, active_provider(), key_status()),
            headers=NO_STORE,
        )

    def run(request: Request, action: Callable[[CredentialStore], Response]) -> Response:
        if (blocked := refused(request)) is not None:
            return blocked
        store = credentials()
        if store is None:
            return unavailable(request)
        try:
            return action(store)
        except CredentialNotFoundError:
            return error_response(request, 404, "credential_not_found", "No key has this ID")
        except CredentialError as exc:
            return error_response(request, 422, "invalid_credential", str(exc))
        except SecretStorageError as exc:
            return error_response(request, 503, "keychain_unavailable", str(exc))

    @application.get("/v1/credentials", response_model=None)
    def list_credentials(request: Request) -> Response:
        def action(store: CredentialStore) -> Response:
            active = active_provider()
            status = key_status()
            document = {
                "kind": "credential_list_response",
                "schema_version": "0.1",
                "active_provider": active,
                "providers": [
                    {
                        "provider": spec.provider,
                        "display_name": spec.display_name,
                        "per_account": spec.per_account,
                        "fields": [
                            {"name": field.name, "label": field.label} for field in spec.fields
                        ],
                    }
                    for spec in CATALOG.values()
                ],
                "credentials": [summary(record, active, status) for record in store.list()],
            }
            validate_document(document)
            return JSONResponse(content=document, headers=NO_STORE)

        return run(request, action)

    @application.post("/v1/credentials", response_model=None)
    def create_credential(request: Request, body: CredentialCreateRequest) -> Response:
        return run(
            request,
            lambda store: respond(
                store.add(
                    body.provider,
                    body.label,
                    body.account_suffix,
                    _plain(body.secrets),
                    body.environment,
                ),
                201,
            ),
        )

    @application.patch("/v1/credentials/{credential_id}", response_model=None)
    def update_credential(
        request: Request, credential_id: CredentialId, body: CredentialUpdateRequest
    ) -> Response:
        changes: dict[str, Any] = {
            name: getattr(body, name) for name in body.model_fields_set
        }  # an explicit null account_suffix clears it
        return run(request, lambda store: respond(store.update(credential_id, **changes)))

    @application.put("/v1/credentials/{credential_id}/secrets", response_model=None)
    def replace_secrets(
        request: Request, credential_id: CredentialId, body: SecretsReplaceRequest
    ) -> Response:
        return run(
            request,
            lambda store: respond(store.replace_secrets(credential_id, _plain(body.secrets))),
        )

    @application.post("/v1/credentials/{credential_id}/move", response_model=None)
    def move_credential(
        request: Request, credential_id: CredentialId, body: MoveRequest
    ) -> Response:
        return run(request, lambda store: respond(store.move(credential_id, body.direction)))

    @application.delete("/v1/credentials/{credential_id}", response_model=None)
    def delete_credential(request: Request, credential_id: CredentialId) -> Response:
        def action(store: CredentialStore) -> Response:
            store.delete(credential_id)
            return Response(status_code=204, headers=NO_STORE)

        return run(request, action)
