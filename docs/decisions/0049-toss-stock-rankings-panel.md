# ADR 0049 — A Rankings panel for Toss stock rankings

- **Status:** accepted (implemented 2026-11-04; not yet checked against a real Toss account).
- **Date:** 2026-11-04

## Context

The user asked for Toss's stock ranking API in SIW Research, as a "Rankings" panel on the right rail. Clicking a ranked stock should open it in the active chart, as the watchlist does.

`/api/v1/rankings` was already readable through the generic Research table (ADR 0017 stage 4). That table shows dotted column names and plain cells, so a symbol cannot be clicked and nothing tells the user which basis a value uses.

The official OpenAPI document is now 1.2.28 (read 2026-11-04 from `openapi.tossinvest.com/openapi-docs/latest/openapi.json`; the code was written against 1.2.24). Its "Ranking" tag has three GET endpoints:

| Endpoint                                            | Options                                                                                                                      | Rows                                                                                                                                                       |
| --------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `/api/v1/rankings`                                  | `type` (6 kinds), `marketCountry` US/KR, `duration` realtime·1d·1w·1mo·3mo·6mo·1y, `excludeInvestmentCaution`, `count` ≤ 100 | `rank`, `symbol`, `currency`, `price.lastPrice/basePrice/changeRate`, `tradingVolume`, `tradingAmount`; `rankedAt`                                         |
| `/api/v1/rankings/investor-trading` (new in 1.2.28) | `count` ≤ 100                                                                                                                | KR only: per investor (foreigner, institution, individual) a `rankedAt`, `netBuyRankings` and `netSellRankings` with `netBuyAmount`/`netSellAmount` in KRW |
| `/api/v1/sectors/rankings`                          | sector rankings                                                                                                              | sectors, not stocks                                                                                                                                        |

What the document says, not yet seen on a real account:

- `TOP_GAINERS` and `TOP_LOSERS` have no `realtime` (400 `unsupported-ranking-duration`).
- `TOSS_SECURITIES_*` count Toss Securities fills only; the others the whole market.
- For gainers and losers, `basePrice` and `changeRate` are against the start of the duration; for the others, against the previous close.
- An unranked choice is an empty list with `rankedAt` null, not an error.
- Investor rankings cover the latest business day only, KRX and NXT together. Foreigners and institutions are provisional during the session; individuals come after the close. Each investor has its own `rankedAt`.

## Decision

- **A typed contract, not the Research table.** `GET /v1/rankings` returns `stock_rankings_response` (`services/market_data/toss_rankings.py`):
  - one ranking: `type` is one of the six Toss kinds, or `NET_BUY` / `NET_SELL` with an `investor`;
  - each row keeps `rank`, `symbol`, `currency`, the price fields, `trading_volume`, `trading_amount` and `net_amount` as the exact decimal strings Toss sent, or null;
  - `ranked_at_utc` is Toss's `rankedAt` in UTC.
- **Choices are checked before any request.** Gainers or losers with realtime, an investor ranking outside KR or with a duration, and a count outside 1–100 are refused with 422. Only documented parameters are sent.
- **Listings come from known lists only.** Each row carries the listing for its symbol:
  - from the configured instruments, then the US symbol directory (Alpaca) or the Korean one (Toss);
  - only lists already on disk are read; a ranking never starts a symbol-list download;
  - a symbol in neither list has `listing: null` and is shown but cannot be opened.
- **Sources stay apart.** The ranking is not chart data and is not stored. The listing only names the instrument; the chart loads its bars from its own source, as after a search.
- **Allowlist.** `/api/v1/rankings/investor-trading` (rate-limit group `RANKING`, 5 per second) joins the read-only Toss allowlist. It is a GET read like the rest.
- **Screen.** A "Rankings" rail button opens the panel, between Stock info and Research. It offers:
  - the ranking, market, period or investor, row count (20, 50, 100), and leaving out caution stocks;
  - a line saying which basis the values use;
  - rank, symbol with name, last price, change (as a percentage), and the ranked value with grouped digits.

  Clicking a symbol works like choosing a search result: the stock is added to the watchlist if missing and shown in the active chart. Realtime is offered disabled for gainers and losers.

- **The Research panel keeps its raw rankings table.** It stays for reading every column Toss sends.

## Alternatives considered

- **Widen the Research panel.** One generic table cannot know which column is the symbol or how to label a basis. Clickable rows would need per-dataset code anyway.
- **Return all three investors and both sides in one response.** That is one Toss call instead of up to six. But each list has its own time, and the panel shows one list at a time. Asking per list is simpler and stays far below 5 per second.
- **Look up unknown symbols through a fresh symbol-list download.** A ranking would then start a download of tens of thousands of listings. Unknown rows stay unclickable instead.

## Consequences

- Clicking a ranked stock adds it to the watchlist, as a search does. The user removes it there.
- Toss's US ticker spelling may differ from Alpaca's for share classes (for example `BRK.B`). Such rows would show without a listing. Not yet seen.
- Korean rows open only when the Toss Korean symbol list has been fetched before (ticker search fetches it).

## Verification

- Tests use the documented payload examples; no real Toss account was used.
- The browser preview was checked against a local fake Toss server; see `docs/PROJECT_STATE.md`.
- **Unverified:** real-account responses, US ticker spelling for share classes, the investor ranking's real times.

## Revisit when

- A live Toss read shows fields or spellings different from the document.
- Toss adds ranking kinds, or a US investor ranking.
- The user wants rankings kept over time. That would need a stored, revisioned dataset with observation times, not this on-demand read.
