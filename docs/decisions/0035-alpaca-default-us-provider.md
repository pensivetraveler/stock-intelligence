# ADR 0035 — Alpaca selected as the default US market-data provider

- **Status:** accepted
- **Date:** 2026-10-22
- **Supersedes in part:** ADR 0012 ("prototype, not a canonical vendor selection") and the rule that Alpaca and Massive stay equal, unselected candidates.

## Context

ADR 0012 added Alpaca as a prototype adapter on 2026-10-17. It was never selected: AGENTS.md, PROJECT_INSTRUCTIONS and SYSTEM_SPEC kept Alpaca and Massive as equal, unselected paid candidates until comparable checks justified a choice.

Since then Alpaca has carried every US screen, backtest and Agent in daily use. On 2026-10-22 the user was shown the evidence below and chose to record Alpaca as the selected default.

### Evidence (from PROJECT_STATE and the ADRs named)

- **Paid SIP:** the user's account is on a paid plan. A SIP request up to `end=now` succeeded, and the app runs with no publication delay (`SIW_ALPACA_DELAY_MINUTES=0`).
  - 2026-10-17 SPY full day (04:00–20:00 ET) backfill: 10 HTTP requests, all 200. 921 of 960 minutes had bars; the other 39 were pre- or after-hours minutes with no trades.
  - IEX volume was 2.4% of SIP for the same window, so SIP is the feed used.
- **Day market (ADR 0030):** `feed=boats` answered 200 with real-time 20:00–04:00 ET bars (AAPL, 288 one-minute bars over 3 sessions). `feed=overnight` is a 400. The BOATS session placed 977 of 1,119 Kiwoom orders that had no confirmed time.
- **Reference data and corporate actions:**
  - adjusted daily bars (`adjustment=split|all`) for backtests (ADR 0021);
  - the asset list (13,228 symbols) for ticker search (ADR 0016);
  - corporate actions (`name_change`, delisting actions) and `asof` reads for ticker changes and 545 delisted instruments (ADR 0033).
- **Failures and limits seen:**
  - daily bars start in 2016, so long weekly requests are `partial`;
  - concurrent chart requests once exceeded the 10-second client timeout (fixed by ordering requests);
  - with the delay at 0, aggregating sparse symbols exposed a 500 in the minute aggregation (fixed);
  - Alpaca exposes no data revisions, and publication time is estimated;
  - the rate limit was never provoked on purpose;
  - the exchange of most delisted instruments is unverified.
- **Massive:** no account, no live call and no adapter. Only the public plan survey in SYSTEM_SPEC and DATA_PROVIDER_EVALUATION exists. No like-for-like comparison was made.

## Decision

- **Default:** Alpaca (feed `sip`, plus `boats` for the day market) is the selected default provider for US bars, symbol search and corporate actions in this workstation.
- **Not a code default:** the code default stays synthetic data. Alpaca is still turned on by `SIW_MARKET_DATA_PROVIDER=alpaca` with the user's keys, so tests and keyless installs never call it.
- **Massive:** stays a registered candidate and possible alternative. Nothing is purchased, subscribed to or implemented without the user's approval.
- **Unchanged rules:**
  - Broker market data (Toss, Kiwoom, KIS, Meritz) stays a separate source, and Korean stocks stay on Toss (ADR 0020).
  - Series from different sources are never merged silently. Every bar keeps its `source_id`, feed and revision.
  - Selecting a default does not verify rights: Alpaca's market-data licence terms for this use are **unverified**.

## Alternatives

- **Keep both unselected:** matches the original rule, but it no longer describes how the product is used, and every decision would keep restating it.
- **Compare with Massive first:** a fairer choice, but it needs a paid Massive account and an adapter. The user chose not to wait for that.

## Trade-offs

- **Selection without a comparable PoC:** the choice rests on Alpaca working, not on beating Massive. Data quality against another consolidated feed is unmeasured; the only cross-checks are against broker data (Toss daily bars matched; Kiwoom minute volume was about 81% of SIP).
- **Single dependency:** an Alpaca outage or plan change affects every US screen. The broker adapters remain as fallbacks for bars, not for corporate actions or symbol history.

## Revisit when

- Alpaca's plan, prices, licence terms or feeds change, or a sustained outage or data error appears;
- the user wants tick, quote or order-book data Alpaca does not offer;
- a Massive (or other) account becomes available for a like-for-like comparison;
- data must be shared beyond this personal workstation (licence review needed first).
