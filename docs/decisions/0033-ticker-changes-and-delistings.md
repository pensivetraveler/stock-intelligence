# ADR 0033 — Ticker changes and delistings

- **Status:** accepted
- **Date:** 2026-10-22

## Context

The Kiwoom sync (ADR 0029) could not match 728 tickers (33,896 orders) to the symbol directory, which lists active stocks on US exchanges only. The user asked:

- for a ticker change, to put the trades on the new ticker's chart;
- for a delisting, to keep the instrument and show its chart up to the delisting.

### What the sources say (checked live on 2026-10-22, counts only)

- **Corporate actions:** Alpaca's `GET /v1/corporate-actions` since 2016 returned 3,479 `name_change` records (old ticker, new ticker, process date) and delisting-like actions (`worthless_removal`, `cash_merger`, `stock_merger`, `redemption`). Its inactive asset list held 2,279 tickers on listed exchanges that no active listing uses.
- **`asof`:** Alpaca's bar API maps a ticker to a company with `asof`, a date on which the ticker belonged to that company. Querying the newest ticker returns the bars from before the change too; META's bars around its 2022 rename from FB came back whole.
- **The unmatched tickers:**
  - 189 were later renamed to an active listed ticker.
  - 263 were renamed to a ticker that is not on a listed exchange. Mostly F-suffix OTC tickers (for example DEVS→DEVSF): the company left its exchange.
  - 270 appear in no rename and on no inactive list. Spot checks (e.g. WINT, TTOO) are companies now trading OTC under the same ticker, which Alpaca lists as active on OTC.
  - 5 are inactive tickers.
  - 1 more was renamed to a ticker that later delisted.

## Decision

### Resolving a broker trade in ticker S on date D (US)

1. **Follow renames:** follow every name change of S processed after D (chains included) to the ticker F the company uses now.
2. **Renamed:** if F is an active listing on a US exchange and F ≠ S, the trade goes on F's chart (`instrument_resolution: renamed`). The execution keeps `symbol` S as the broker reported it.
3. **Normal:** if S is an active listed ticker and was not renamed after D, nothing changes.
4. **Delisted:** otherwise the company no longer trades on a US exchange; it was delisted, often to OTC. The trade keeps its own instrument `us:S:<venue>:2026` (`instrument_resolution: delisted`).
   - **Venue:** comes from the inactive asset list when it names a listed exchange. Otherwise it is XNAS and is marked unverified, because Kiwoom reports no exchange.
   - **Registry:** the instrument goes into `SIW_DATA_DIR/symbols/delisted-instruments.json` with `asof` = the latest trade date seen for it and the ticker it moved to, if any.
5. **Reused tickers:** when S is delisted under rule 4 but another company uses S today, the delisted company gets its own ID with a year suffix: `us:S.Dyy:<venue>:2026`.
   - **The year:** the year it was renamed away when known, otherwise the year of its first trade seen. Later trades of the same company reuse that ID.
   - **Charts:** `asof` and the Alpaca ticker are looked up per instrument, not per ticker, so today's S keeps its own chart.
   - **Added after review:** a first version shared one ID per ticker. Today's DXF chart then took the 2023 company's `asof`.
6. **Not resolved:** a ticker an active company uses now, which another company used at D without a recorded rename or exchange move, is not detected. It resolves as normal (rule 3). This is a known limitation.

### Charts

- **Delisted instruments:** bars are requested with Alpaca `asof` = the registry date, so the ticker maps to the company that was traded. SIP covers listed exchanges only, so the bars end where exchange trading ended. Later OTC prints are not included (they would need Alpaca's separate OTC feed).
- **Renamed tickers:** need nothing extra. The new ticker's bars include the old ticker's years.
- **Search:** symbol search also lists registered delisted instruments, marked as delisted with their last trade date. A search for an old ticker shows the ticker it was renamed to.

### Storage and refresh

- **History file:** `SIW_DATA_DIR/symbols/history-<source>.json`, refreshed when more than a day old; a failed refresh keeps the previous copy.
- **Delisted instruments:** the registry is written by the broker syncs. A re-sync stores the trades it can now resolve. Trades already stored are never changed; a different resolution would be reported as a conflict.

### Checks after the decision (2026-10-22, counts only)

- **Toss reports today's ticker:** of the 5,710 stored Toss executions, 177 (18 tickers) were filled before a recorded rename of their ticker's company, and every one of them carries the new ticker. None carries an old ticker. Following renames is therefore not needed for Toss; Toss sync keeps its plain directory lookup. Toss trades of delisted companies were not checked.
- **Venue of delisted instruments:** Alpaca SIP trades queried with `asof` = the registry date carry the tape of the primary listing (`z`: A = NYSE, B = NYSE American, NYSE Arca, Cboe and other exchanges, C = Nasdaq). For the 7 instruments with a verified venue, the tape agreed every time. Of the 534 unverified ones (all set to XNAS): 435 tape C (Nasdaq, as set), 56 tape A (NYSE), 38 tape B (not Nasdaq; the exchange inside tape B cannot be told apart), 5 without data. The 94 that are not Nasdaq would need new IDs, which re-keys stored trades; that waits for the user's decision.

## Alternatives considered

- **Instrument IDs per company instead of per ticker:** the right long-term identity, but every stored bar, drawing and layout keys on ticker IDs today.
- **Alpaca's OTC feed for prints after a delisting:** a separate subscription. On 2026-10-22 one read-only request (`feed=otc`, daily bars) with the user's key was refused: 403 "subscription does not permit querying OTC data".

## Revisit when

- Ticker reuse turns up in the user's trades, or IDs move to per-company identity.
- OTC data is wanted after a delisting.
