import type {
  CredentialListResponse,
  CredentialSummary,
} from "@siw/contracts/types";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { describe, expect, it, vi, type Mock } from "vitest";

import { createCredentialApi, type CredentialApi } from "./credential-api";
import { KeysPanel } from "./KeysPanel";
import { MarketApiError } from "./market-api";

const SECRET = "test-secret-not-real";

function key(changes: Partial<CredentialSummary> = {}): CredentialSummary {
  return {
    kind: "credential_summary",
    schema_version: "0.1",
    credential_id: "0123456789abcdef0123456789abcdef",
    provider: "kis",
    label: "해외주식",
    account_suffix: "1234",
    priority: 1,
    enabled: true,
    active: true,
    created_at_utc: "2026-10-19T01:00:00Z",
    updated_at_utc: "2026-10-19T01:00:00Z",
    status: {
      state: "cooling",
      cooling_until_utc: "2026-10-19T14:01:00Z",
      last_error: "KIS rate limit reached; retry later",
      last_used_at_utc: null,
    },
    ...changes,
  };
}

function listing(credentials: CredentialSummary[]): CredentialListResponse {
  return {
    kind: "credential_list_response",
    schema_version: "0.1",
    active_provider: "kis",
    providers: [
      {
        provider: "alpaca",
        display_name: "Alpaca",
        per_account: false,
        fields: [
          { name: "api_key_id", label: "API key ID" },
          { name: "api_secret_key", label: "API secret key" },
        ],
      },
      {
        provider: "kis",
        display_name: "한국투자증권",
        per_account: true,
        fields: [
          { name: "app_key", label: "App key" },
          { name: "app_secret", label: "App secret" },
        ],
      },
    ],
    credentials,
  };
}

type MockedCredentialApi = {
  [Name in keyof CredentialApi]: Mock<CredentialApi[Name]>;
};

function fakeApi(initial: CredentialSummary[]): MockedCredentialApi {
  let credentials = initial;
  return {
    list: vi.fn<CredentialApi["list"]>(() =>
      Promise.resolve(listing(credentials)),
    ),
    add: vi.fn<CredentialApi["add"]>((request) => {
      const created = key({
        credential_id: "f".repeat(32),
        label: request.label,
        account_suffix: request.account_suffix,
        priority: credentials.length + 1,
        status: null,
      });
      credentials = [...credentials, created];
      return Promise.resolve(created);
    }),
    update: vi.fn<CredentialApi["update"]>(() => Promise.resolve(key())),
    replaceSecrets: vi.fn<CredentialApi["replaceSecrets"]>(() =>
      Promise.resolve(key()),
    ),
    move: vi.fn<CredentialApi["move"]>(() => Promise.resolve(key())),
    remove: vi.fn<CredentialApi["remove"]>(() => {
      credentials = [];
      return Promise.resolve();
    }),
  };
}

describe("KeysPanel", () => {
  it("lists keys with masked accounts and rate-limit status, never secrets", async () => {
    render(<KeysPanel api={fakeApi([key()])} />);

    const row = (await screen.findByText("해외주식")).closest("tr");
    expect(row).not.toBeNull();
    const cells = within(row as HTMLElement);
    expect(cells.getByText("****1234")).toBeInTheDocument();
    expect(
      cells.getByText(/Rate limited until .* ET · KIS rate limit reached/),
    ).toBeInTheDocument();
    expect(screen.getByText(/· in use/)).toBeInTheDocument();
  });

  it("adds a per-account key and clears the secret fields", async () => {
    const api = fakeApi([]);
    render(<KeysPanel api={api} />);
    const form = await screen.findByRole("form", { name: "Add key" });
    await waitFor(() =>
      expect(within(form).getByLabelText("Provider")).toHaveValue("kis"),
    );

    fireEvent.change(within(form).getByLabelText("Label"), {
      target: { value: "연금계좌" },
    });
    fireEvent.change(within(form).getByLabelText("Account (last 4 digits)"), {
      target: { value: "5678" },
    });
    const appKey = within(form).getByLabelText("App key");
    const appSecret = within(form).getByLabelText("App secret");
    expect(appSecret).toHaveAttribute("type", "password");
    expect(appSecret).toHaveAttribute("autocomplete", "off");
    fireEvent.change(appKey, { target: { value: "test-key" } });
    fireEvent.change(appSecret, { target: { value: SECRET } });
    fireEvent.click(within(form).getByRole("button", { name: "Add key" }));

    await waitFor(() =>
      expect(api.add).toHaveBeenCalledWith({
        provider: "kis",
        label: "연금계좌",
        account_suffix: "5678",
        secrets: { app_key: "test-key", app_secret: SECRET },
      }),
    );
    await screen.findByText("****5678");
    expect(appSecret).toHaveValue("");
    expect(appKey).toHaveValue("");
  });

  it("hides the account field for a vendor key", async () => {
    render(<KeysPanel api={fakeApi([])} />);
    const form = await screen.findByRole("form", { name: "Add key" });
    await waitFor(() =>
      expect(within(form).getByLabelText("Provider")).toHaveValue("kis"),
    );
    fireEvent.change(within(form).getByLabelText("Provider"), {
      target: { value: "alpaca" },
    });
    expect(
      within(form).queryByLabelText("Account (last 4 digits)"),
    ).not.toBeInTheDocument();
    expect(within(form).getByLabelText("API secret key")).toBeInTheDocument();
  });

  it("asks for confirmation before deleting", async () => {
    const api = fakeApi([key()]);
    render(<KeysPanel api={api} />);
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));
    expect(api.remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Confirm delete" }));
    await waitFor(() =>
      expect(api.remove).toHaveBeenCalledWith(key().credential_id),
    );
    await waitFor(() =>
      expect(screen.queryByText("해외주식")).not.toBeInTheDocument(),
    );
  });

  it("explains when the API has no data directory", async () => {
    const api = fakeApi([]);
    api.list.mockRejectedValue(
      new MarketApiError("off", "credential_storage_unconfigured"),
    );
    render(<KeysPanel api={api} />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "start the API with SIW_DATA_DIR",
    );
  });
});

describe("createCredentialApi", () => {
  it("sends secrets once and deletes with DELETE", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify(key()), {
          status: 201,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const api = createCredentialApi("http://api", fetcher);

    await api.add({
      provider: "kis",
      label: "a",
      account_suffix: null,
      secrets: { app_key: "k", app_secret: SECRET },
    });
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe("http://api/v1/credentials");
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body)).secrets.app_secret).toBe(SECRET);

    await api.remove(key().credential_id);
    expect(fetcher.mock.calls[1][0]).toBe(
      `http://api/v1/credentials/${key().credential_id}`,
    );
    expect(fetcher.mock.calls[1][1]?.method).toBe("DELETE");
  });
});
