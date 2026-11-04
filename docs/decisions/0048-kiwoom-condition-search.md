# ADR 0048 — Kiwoom US condition search (조건검색)

- **Status:** accepted. The user asked to connect Kiwoom's condition search (2026-11-04) after a survey of broker screener APIs.
- **Date:** 2026-11-04

## Context

The user is building a ticker screener and keeps 76 US conditions in 영웅문Global. Kiwoom's REST API runs saved conditions but cannot create or edit them. The calls go over Kiwoom's WebSocket (`wss://api.kiwoom.com:10000/api/us/websocket`), not over REST:

| api-id     | `trnm`               | What it does                                               |
| ---------- | -------------------- | ---------------------------------------------------------- |
| `usa20280` | `GCNSRLST`           | The saved conditions: `[seq, name]` pairs                  |
| `usa20281` | `GCNSRREQ`, type `0` | One run of a condition, paged by body `cont_yn`/`next_key` |
| `usa20290` | `GCNSRREQ`, type `1` | A run plus realtime enter/leave events                     |
| `usa20291` | (not read)           | Stops those events                                         |

A connection first sends `{"trnm": "LOGIN", "token": <REST access token>}` and waits for a `LOGIN` reply with `return_code` 0. The server sends `PING` messages that must be echoed back.

Live check on 2026-11-04 with the user's key (kwcli, read-only): the list returned 76 conditions, with gaps in `seq`. Condition 42 returned 10 rows in one page. The replies used `trnm` `GCNSRLST` and `GCNSRREQ`, but the spec names them `GCNSRLT` and `CNSRREQ`, so both spellings are accepted. Numbers come zero-padded with their sign (`"000005.1600"`, `"-00000.0265"`, `"0009011562"`).

A row has no time. The spec does not say whether its price is the regular close, the latest extended-hours trade or a delayed value. `stex_tp` is Kiwoom's exchange code (`ND`, `NY`, `NA`), and ADR 0012 found that `NY` also covers NYSE Arca ETFs, so the venue cannot be read from it.

## Decision

- **US only, one run at a time.** The API lists the conditions and runs one when asked. Realtime events (`usa20290`) and the domestic conditions (`ka10171`–`ka10174`) are left for later.
- **One connection per call, opened by the API process.** The connection logs in, sends `GCNSRLST` so it has the condition's name and knows the `seq` exists, then pages `GCNSRREQ` with at most 20 pages, and closes. The token comes from the same real-account Kiwoom key and session as the order-history sync. The client sends only `LOGIN`, `PING` echoes, `GCNSRLST` and `GCNSRREQ` with `search_type` `0`, the way `AllowlistedClient` limits REST paths. Nothing on this socket can place an order.
- **Nothing is stored.** A result is a point-in-time read like a quote (`broker_condition_result`), stamped with the time the API received it (`fetched_at_utc`). It is not a bar, a quote or evidence. Backtests, replay and agents never read it, because it cannot be reproduced for a past time: the condition lives in Kiwoom's HTS and can change without SIW knowing.
- **Tickers are matched, never guessed.** Each row's ticker goes through the app's own instrument lookup (configured instruments first, then the symbol directory, with share-class forms such as `SI-A`). A row that does not match keeps `listing: null` and cannot be opened on a chart. Kiwoom's exchange code is reported as `kiwoom_exchange` and is not used to pick a venue.
- **Endpoints:** `GET /v1/screeners/kiwoom/us/conditions` returns `broker_condition_list`. `POST /v1/screeners/kiwoom/us/conditions/{seq}/run` returns `broker_condition_result`. Errors follow the other broker reads: 404 when no real-account Kiwoom key is set or the `seq` is unknown, 502 when login fails, 503 when Kiwoom is unavailable or rate-limited.
- **Screen:** a 조건검색 side panel in the Research app. The user picks a condition and runs it, and the rows show Kiwoom's figures with the time of the run. Clicking a matched ticker opens it on the active chart, the way search results do.

## Alternatives

- **Computing the screen from Alpaca data:** this is reproducible and fits backtests, but it means building the user's 76 conditions again. It stays the long-term path for screens used in strategies.
- **Keeping one connection open for every call:** this saves a login per run, but needs reconnect and token-refresh logic for a call that runs only when the user asks. It will come back with realtime events.
- **Taking the venue from `stex_tp`:** this is wrong for Arca ETFs (`NY`).

## Revisit when

- the user wants realtime enter/leave events, or domestic conditions;
- a strategy or agent needs a screen result. It would then need a stored, versioned screen that SIW computes itself;
- Kiwoom documents the time of a row's figures.
