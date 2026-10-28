# ADR 0043 — 메리츠증권 Open API: account, fills, daily bars, quote and realtime (read-only)

- **Status:** accepted (built from the official spec; live account check pending)
- **Date:** 2026-10-26

## Context

- **The ask:** the user asked for the 메리츠증권 Open API to be connected (openapi.imeritz.com, general availability 2026-10-26). From the list offered, they chose all four parts:
  - order and fill history sync;
  - account holdings and cash;
  - daily bars and the current price;
  - realtime quotes over WebSocket.
- **Orders left out:** the order APIs were left out of scope at the start. Meritz documents no demo server, so any order would be a real one. AGENTS.md and ADR 0036 allow real-account orders only after a separate approval.
- **What existed:** a minute-bar adapter (ADR 0013, 2026-10-17), never checked with a live account.
- **Spec:** JSON spec downloaded 2026-10-26 (`/api-spec/download/json`, generated 2026-10-26 21:44): 65 REST and 14 WebSocket services.

## Decision

### One read-only session per key (`meritz_session.py`)

- **Token:** a form POST with `scope=login`; `expires_in` is in seconds.
- **Headers:** every call sends `authorization: Bearer …` and `mac_address`, as the spec requires.
- **What can be sent:** only the token path and the listed GET queries. The allowlist has no order, order change, reservation or currency-exchange path. Anything else raises before a request is sent.
- **Success and errors:** every error comes back as HTTP 500, so success is judged by `rsp_cd`:
  - "0000" for quotes;
  - "5766" for account queries;
  - "5762" when more pages follow;
  - "5820" (no rows) is an empty reply;
  - "EGW00200" is the per-second limit.
- **Token retry:** an HTTP 401 or 403 is retried once with a new token. The spec names no token error code.
- **Paging:** `tr_cont` "1" with `tr_cont_key`, sent back unchanged. The key can hold long runs of spaces.
- **Shared session:** one session per key for the whole process, so the chart, the account view and the relay share one token.

### MAC address with the key

- **Where it is kept:** the Keys screen's Meritz form gets a third field, the MAC address (12 hex digits). It is stored in the OS keychain with the key it is used with. It is not secret, but it is per device and every call needs it.
- **Existing keys:** a Meritz key registered before this change lacks the field. It is reported as incomplete ("enter it again").
- **Never guessed:** the MAC address is not detected automatically, and it never appears in errors, `repr` or the ledger.

### Fill history into the broker ledger (`meritz_account.py`)

- **Source:** `GET /trading/v1/overseas/orders/history` (TR obtro_oder_201r), filled orders only (`cntg_cls_code` "1"), over the order-date range in one paged query. The range may start at most five years back.
- **One record per order:** each row becomes one `broker_order_execution`:
  - `granularity: order_aggregate`, with the filled quantity and the fill price;
  - `time_meaning: reported_fill_time`, from the single `cntg_ymdh`. The spec does not say whether it is the first or the last fill.
- **Corrections:** a correction row is an order of its own (its own order number), stored with status `…_AMENDED`.
- **No fees:** the history reports none per order, so fees are null, never zero.
- **Skipped rows:**
  - not filled;
  - not USD, or not the US market;
  - unknown ticker.
- **Times (not documented):** `cntg_ymdh` gives no time zone. It is read as **Korean time** until a live check settles it (`HISTORY_ZONE`).
  - A time that lands in no US session is skipped as `time_unresolved` rather than dated by guess. A US session here is 04:00–20:00 New York on a trading day, or the day market before it (ADR 0030).
  - The sync result's note on the screen says the time zone is not yet confirmed.
- **Account reference:** the token is bound to one account. The account number is never read. The account is `meritz-<hash of the app key>`.

### Account snapshot

`GET /v1/brokers/meritz/account` returns the same `broker_account_snapshot` contract as Toss (`broker_id` now `toss` or `meritz`). It is built from:

- **Holdings:** `/accounts/v1/me/overseas/holdings`, USD rows.
- **USD buying power:** `/accounts/v1/me/overseas/currencies` (estimated USD cash).
- **KRW totals:** `/accounts/v1/me/valuation` (purchase and asset value).
- **KRW buying power:** `/accounts/v1/me/deposit` (cash orderable).
- **Open orders:** `/trading/v1/overseas/orders/today`, unfilled rows with quantity left.

A part that fails is listed in `errors` and the rest is shown. There are no commission rates or conditional orders: they are not in the allowlist.

### Daily bars and quote

- **Daily bars:** `/market/v1/overseas/candles/days` with `from`/`to`, at most a year per request.
  - Prices as traded (`mod_stpr_cls_code` "1") are `raw`. "0" gives `split_adjusted`; it is never cached, and dividends are not adjusted.
  - A day with zero volume is not a bar. The spec's example shows such a placeholder for the day not yet traded.
  - `date` is taken as the New York trading date (not documented). Bars are labelled with the regular-session span, like other US providers; whether Meritz includes extended hours is not documented.
  - Any US listing on NASDAQ, NYSE, AMEX or ARCA can be asked for, not only the configured symbols.
- **Quote:** `GET /v1/market/quote?source=meritz`, and the fallback for US listings when no Toss key is registered.
  - The current-price reply has no time, so the last price is the newest tick's. Ticks carry Korean clock times without a date; each is placed on the latest instant not after the newer one.
  - The order book has no time either: its `at_utc` is when it was read, and the screen says so.
  - Delayed books (all zeros) are shown as no book.
  - US listings have no price limits.

### Realtime relay (`meritz_stream.py`)

- **Endpoint:** `/v1/realtime?source=meritz` opens one Meritz WebSocket for the app's clients, apart from the Toss relay. The two are never mixed in one connection.
- **Subscriptions per symbol:** trades for the regular session (`ohhts_exst_cntg`), after-hours (`ohhts_exst_cnta`) and the daytime session (`ohhts_exst_cntd`), plus the 10-level book (`ohhts_exst_aspr`). The key is `<market>@<ticker>`, for example `OQ@AAPL`.
- **Limits:** at most 10 symbols, because the session limit (WSC00110) is not published.
- **Times:** taken from `korea_date` + `korea_hour`, which name their zone.
- **Ticks:** stored as `lossy_stream` under `meritz-us`, like Toss's.
- **Not subscribed:** the order notification service. Its messages carry the account number and holder name.

### Screen

- **Account panel:** shows the Meritz account next to Toss's, with a "메리츠 미국주식 체결 동기화" button. The "from" date is shared with Kiwoom. The panel title is now "Broker accounts".
- **Live quote panel:** a Toss / Meritz source choice. It decides both the polled quote and the realtime relay, and is remembered in the browser (`siw.liveSource`). Live data is cleared when the source changes.
- **Charts:** Meritz as the chart provider (Keys screen) now serves daily bars too.

## Alternatives considered

- **Reading history times as New York time:** a regular-session fill would then read as a day-market fill, or the other way round. Neither guess is safe, so Korean time is assumed with session checks, until the live check settles it.
- **Merging Meritz trades into the Toss relay:** one stream, but two sources' prints in one view. Rejected, as in ADR 0030.
- **Order APIs behind a switch:** there is no demo server to build against. This waits for a separate decision (ADR 0036 gates).

## Remaining risks

- **Not checked live:** the history time zone, the daily `date` meaning and session span, the token error codes, the WebSocket handshake (whether it needs headers) and the session limit.
- **Fees:** fees per order are not reported. Positions show them as unreported.
- **Realtime limit:** at most 10 symbols at once.

## Revisit when

- A live check confirms or refutes the history time zone (change `HISTORY_ZONE` and re-sync).
- Meritz publishes a demo server or the user approves real-account orders (ADR 0036).
- Fees per order are needed: `/trading/v1/overseas/orders/today` has `oder_cfee` for today only.
