import type {
  CredentialListResponse,
  CredentialSummary,
} from "@siw/contracts/types";

import { callApi, callForKind, type ApiTarget } from "./market-api";

export type KeyedProvider = CredentialSummary["provider"];

export interface CredentialCreateRequest {
  provider: KeyedProvider;
  label: string;
  account_suffix: string | null;
  secrets: Record<string, string>;
}

export interface CredentialUpdateRequest {
  label?: string;
  account_suffix?: string | null;
  enabled?: boolean;
}

/**
 * Keys screen client. Secret values are only ever sent, never read back: every
 * response is a `credential_summary` without them.
 */
export interface CredentialApi {
  list(): Promise<CredentialListResponse>;
  add(request: CredentialCreateRequest): Promise<CredentialSummary>;
  update(
    credentialId: string,
    request: CredentialUpdateRequest,
  ): Promise<CredentialSummary>;
  replaceSecrets(
    credentialId: string,
    secrets: Record<string, string>,
  ): Promise<CredentialSummary>;
  move(
    credentialId: string,
    direction: "up" | "down",
  ): Promise<CredentialSummary>;
  remove(credentialId: string): Promise<void>;
}

export function createCredentialApi(
  baseUrl: string,
  fetcher: typeof fetch = fetch,
  timeoutMs = 10_000,
): CredentialApi {
  const target: ApiTarget = { baseUrl, fetcher, timeoutMs };
  const path = (credentialId: string) =>
    `/v1/credentials/${encodeURIComponent(credentialId)}`;
  const summary = (
    url: string,
    method: string,
    body: unknown,
  ): Promise<CredentialSummary> =>
    callForKind<CredentialSummary>(target, url, "credential_summary", {
      method,
      body,
    });
  return {
    list() {
      return callForKind<CredentialListResponse>(
        target,
        "/v1/credentials",
        "credential_list_response",
      );
    },
    add(request) {
      return summary("/v1/credentials", "POST", request);
    },
    update(credentialId, request) {
      return summary(path(credentialId), "PATCH", request);
    },
    replaceSecrets(credentialId, secrets) {
      return summary(`${path(credentialId)}/secrets`, "PUT", { secrets });
    },
    move(credentialId, direction) {
      return summary(`${path(credentialId)}/move`, "POST", { direction });
    },
    async remove(credentialId) {
      await callApi(target, path(credentialId), { method: "DELETE" });
    },
  };
}
