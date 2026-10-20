# ADR 0014 — Provider key registry (Keys screen, OS keychain, rate-limit failover)

- **Status:** accepted (implemented; keychain behaviour checked in tests with an in-memory backend, not yet with the live macOS keychain)
- **Date:** 2026-10-19

## Context

Until now each provider read exactly one key pair from environment variables (ADR 0012, ADR 0013). That does not fit how the keys are issued:

- **Vendor APIs (Alpaca):** keys belong to the login.
- **Broker APIs:** keys are issued per brokerage account, so one broker can have several.

The user asked for a screen to manage keys. On 2026-10-19 they chose:

- secrets are entered on the screen and stored in the OS keychain;
- `.env` is no longer a source of keys;
- an account is identified only by a label plus the last 4 digits of its number;
- when one key hits a rate limit, the next key takes over.

## Decision

- **Split storage:**
  - Secret values go to the OS keychain through `keyring` (service `stock-intelligence-workstation`, one item per key named `<provider>:<credential_id>`, value a JSON object of that provider's fields).
  - `SIW_DATA_DIR/credentials.sqlite3` holds only the label, account suffix, order, enabled flag, secret revision and timestamps. It is a separate file from the market cache and the journal.
  - Registration writes the keychain first and removes the item again if the row cannot be written, so a row never points to a missing secret.
- **Write-only secrets:**
  - Secrets travel in through `POST /v1/credentials` and `PUT /v1/credentials/{id}/secrets` and are read back only to build an adapter.
  - Responses use the new `credential_summary` and `credential_list_response` contracts. These contracts have no field for secret values (`additionalProperties: false`) and accept only a 4-digit account suffix.
  - Responses are sent with `Cache-Control: no-store`. Validation errors stay generic, so input is never echoed.
- **Local-only access:** the credential routes return 403 `credential_request_refused` in two cases:
  - the `Host` is not a loopback name, which blocks DNS rebinding;
  - an `Origin` is present and is not in the CORS allowlist, which blocks other web pages open in the user's browser.
  - CORS now also allows `PUT`, `PATCH` and `DELETE`, but only for the same allowlisted origins.
- **No keys from the environment:** `catalog.adapter_settings` removes every key variable from the environment before it adds one registered key's values. Adapters keep their `from_env` parsing for non-secret settings (symbols, sessions, `SIW_KIWOOM_TIME_ZONE`, `SIW_MERITZ_MAC_ADDRESS`). `SIW_MARKET_DATA_PROVIDER` still selects the running provider. Every keyed provider requires `SIW_DATA_DIR`.
- **Key pool (`PooledProvider`):**
  - One adapter per enabled key, used in the user's order.
  - **Failover:** on `ProviderRateLimitError` that key cools down for 60 seconds and the same request goes to the next key. When every key is limited, the provider returns 503 `provider_rate_limited`.
  - **No masking:** any other error (auth, data, outage) is raised as is, so a broken key is never hidden behind a working one.
  - **No load spreading:** requests are not rotated across keys to raise throughput (see Alternatives).
  - **Live changes:** the pool reads the registry on every request. An adapter is rebuilt only for a new key or a replaced secret, so access tokens stay in memory (KIS sends a KakaoTalk notice on every issuance).
  - **Status:** runtime state (idle, ok, cooling until, last error) appears on the Keys screen for the running provider's keys.
- **CLI:** `broker_check` and `alpaca check` use the registered keys and accept `--label` to test one key.
- **Unchanged:**
  - Adapters stay read-only through `AllowlistedClient`.
  - Tokens are never written to disk.
  - Market data from different providers is never merged.

## Alternatives

- **Keep keys in `.env` with numbered names:** this was simpler, and secrets would never pass through the API. The user preferred registering keys on the screen.
- **Encrypted file with a master password:** this would be portable between machines, but needs a password prompt on every start and a key-derivation design. The OS keychain already protects the secrets with the user's login.
- **Round-robin across keys from the start:** this would give more throughput. But brokers set limits per customer or app, so spreading one workload over several account keys can count as circumventing those limits. It stays off until each broker's terms are checked; failover only reacts to a limit that was hit.
- **Store full account numbers:** these are not needed for market data. They would be needed later for account APIs (fills, balances), which require a separate decision.

## Trade-offs and risks

- **Unverified keychain platforms:** the live macOS keychain, Windows Credential Manager and Linux Secret Service are not yet verified. Tests use an in-memory backend. On a machine without a keychain backend, registration fails with 503 `keychain_unavailable`.
- **Orphaned item:** if the keychain item cannot be deleted after its row is removed, the item stays behind and the error says so.
- **Cooldown length:** 60 seconds is a fixed guess. Broker limits are per second (KIS, Toss, Meritz) or undocumented (Kiwoom), so the guess is conservative.
- **Toss token conflict:** two keys of the same Toss client still revoke each other's token (ADR 0013). Separate accounts have separate clients.

## Revisit when

- A broker's terms are confirmed to allow several account keys for one user's market data. Optional rotation could then be enabled for that broker.
- Account APIs (fills, balances) are added. Full account numbers and a per-key purpose would then be needed.
- Keys must be shared with a NAS or cloud collector. The keychain is per machine.

## Amendment 2026-10-20 — real and demo accounts

The user asked that every broker key record whether its account is a real (실전) or a demo (모의투자) account. Real and demo keys are issued separately and only work against the matching server.

- **Storage:** credentials migration `0002` adds `environment` (`real` | `demo`). It is required for per-account (broker) keys and absent for vendor keys (Alpaca). Broker keys registered earlier were all used against the real domains, so the migration marks them `real`. New keys default to demo on the Keys screen. The type can be changed later; the pool then rebuilds that key's adapter.
- **Contract and API:** `credential_summary.environment` is `real`, `demo` or null, and `POST`/`PATCH /v1/credentials` accept `environment`.
- **Routing:** the account type reaches an adapter as `SIW_<PROVIDER>_ENVIRONMENT`, taken only from the registered key and never from the process environment.
  - **Kiwoom:** demo goes to `https://mockapi.kiwoom.com` (from the official API spec).
  - **KIS:** demo goes to `https://openapivts.koreainvestment.com:29443` (`vps` in the official `kis_devlp.yaml`). The `SIW_*_BASE_URL` override applies to real keys only.
  - **Toss, Meritz:** their documentation names no demo domain, so their adapters refuse demo keys with a clear error rather than sending them to the real server. The key shows that error and the next key is used.
- **No mixing:** bars from a demo server get their own feed scope and revision (`kiwoom-us-demo`, `kis-us-demo`). They are therefore never combined or cached together with real data.
- **Kiwoom MCP:** `scripts/kiwoom_mcp_launcher.py` starts Kiwoom's `kiwoom-exec` MCP server with the first enabled Kiwoom key from the Keys screen. It sets `KIWOOM_MODE` from that key's type, so the key and server always match.
- **Unverified:** no demo key has been tried against either demo server yet, and it is unknown whether the demo servers serve US minute or daily charts.
