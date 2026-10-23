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
   - **Verified later:** a venue marked verified in the registry wins over the XNAS default, so a later trade keeps the same ID. Added on 2026-10-22 after the SIP tape check below.
   - **Registry:** the instrument goes into `SIW_DATA_DIR/symbols/delisted-instruments.json` with `asof` = the latest trade date seen for it and the ticker it moved to, if any.
5. **Reused tickers:** when S is delisted under rule 4 but another company uses S today, the delisted company gets its own ID with a year suffix: `us:S.Dyy:<venue>:2026`.
   - **The year:** the year it was renamed away when known, otherwise the year of its first trade seen. Later trades of the same company reuse that ID.
   - **Charts:** `asof` and the Alpaca ticker are looked up per instrument, not per ticker, so today's S keeps its own chart.
   - **Added after review:** a first version shared one ID per ticker. Today's DXF chart then took the 2023 company's `asof`.
6. **Not resolved automatically:** a ticker an active company uses now, which another company used at D without a recorded rename or exchange move, is not detected. It resolves as normal (rule 3). A price check can find such cases, and a registry entry with `reused_on` then fixes them (follow-up 2026-10-23).

### Charts

- **Delisted instruments:** bars are requested with Alpaca `asof` = the registry date, so the ticker maps to the company that was traded. SIP covers listed exchanges only, so the bars end where exchange trading ended. Later OTC prints are not included (they would need Alpaca's separate OTC feed).
- **Renamed tickers:** need nothing extra. The new ticker's bars include the old ticker's years.
- **Search:** symbol search also lists registered delisted instruments, marked as delisted with their last trade date. A search for an old ticker shows the ticker it was renamed to.

### Storage and refresh

- **History file:** `SIW_DATA_DIR/symbols/history-<source>.json`, refreshed when more than a day old; a failed refresh keeps the previous copy.
- **Delisted instruments:** the registry is written by the broker syncs. A re-sync stores the trades it can now resolve. Trades already stored are never changed; a different resolution would be reported as a conflict.

### Checks after the decision (2026-10-22, counts only)

- **Toss reports today's ticker:** of the 5,710 stored Toss executions, 177 (18 tickers) were filled before a recorded rename of their ticker's company, and every one of them carries the new ticker. None carries an old ticker. Following renames is therefore not needed for Toss; Toss sync keeps its plain directory lookup. Toss trades of delisted companies were not checked.
- **Venue of delisted instruments:** Alpaca SIP trades queried with `asof` = the registry date carry the tape of the primary listing (`z`: A = NYSE, B = NYSE American, NYSE Arca, Cboe and other exchanges, C = Nasdaq). For the 7 instruments with a verified venue, the tape agreed every time. Of the 534 unverified ones (all set to XNAS): 435 tape C (Nasdaq, as set), 56 tape A (NYSE), 38 tape B (not Nasdaq; the exchange inside tape B cannot be told apart), 5 without data. The 94 that are not Nasdaq would need new IDs, which re-keys stored trades.
- **Re-keyed with the user's approval (2026-10-22):** the 56 tape A instruments moved to XNYS (`venue_verified: true` in the registry). The 38 tape B ones stay XNAS and unverified, as do the 5 without data. The 435 tape C ones keep XNAS; their registry flag was not changed.

### Follow-up (2026-10-23)

- **Toss symbols missing from the directory:** the Toss sync skipped 506 filled orders on 31 symbols. Toss reports today's ticker, and for a delisted company that is often its OTC ticker (MMATQ for a trade made as MMAT).
  - **Rule:** Toss trades still use the plain directory lookup first, so stored Toss records do not change. A US symbol the directory does not know is traced back through the renames processed after the trade's New York date (`SymbolHistory.traded_as`, newest rename first) to the ticker traded that day. That ticker is then resolved by rules 1–5, and the execution records `instrument_resolution`.
  - **Live result (counts only):** all 506 orders were stored as `delisted`, with no conflicts and no symbols left unknown. Two symbols (XTKGF, EMWPF) have no recorded rename and keep their OTC ticker as a delisted instrument whose venue is not verified. If those were OTC trades after the delisting, the chart has no bars for them.
  - **Toss sync result:** now lists `unknown_symbols` like the Kiwoom sync.
- **Venue from the closing cross:** for the 478 instruments still unverified, the largest SIP closing print (condition 6) at 16:00 New York on the instrument's last bar day was read with `asof`. On a listing exchange the closing auction is that print.
  - **NYSE American:** 27 tape B instruments crossed there. With the user's approval they moved from XNAS to XASE. Backups were made first: `journal-before-xase-rekey-20261023T075532Z.sqlite3` and the registry copy. Their 1,652 executions (1,629 Kiwoom, 23 Toss) and 1,014 fee lines were deleted and synced again, with the same execution and line IDs and no conflicts. Row counts are unchanged, and no row uses the old IDs.
  - **Nasdaq:** 397 tape C instruments crossed on Nasdaq. With the user's approval they are now marked verified at XNAS (registry only).
  - **Still unverified:** 10 tape B instruments without a cross, 1 that crossed on another venue, 25 tape C ones without a Nasdaq cross, and 18 without data.
- **Ticker reuse (rule 6): evaluated by price.**
  - **Method:** every stored US fill without a resolution was compared with the raw SIP 1-hour bars in the hour around it, three hours in all. Day-market fills were excluded. A first pass with daily bars flagged hundreds of instruments, but those were extended-hours fills outside the regular-session range.
  - **Result:** 69,855 fills were checked and 16 fell outside their bars. One instrument was a real case. STRR: 8 Kiwoom fills on 2023-05-05 at about $1, while the STRR chart showed about $22. The old Star Equity merged away on 2025-08-22, and Hudson Global renamed HSON→STRR on 2025-09-05. The trade side had no rename away, so rules 1–5 kept the fills on today's STRR. The other 3 flagged instruments had 1–2 fills each outside.
  - **A rule from the renames alone was rejected.** It would say "a rename into S after D means S belonged to another company on D". It matched 1,336 fills on 37 tickers. Toss reports today's ticker, so its trades all match (META for FB fills). Kiwoom fills such as WORX and CXAI matched their charts by price.
  - **Registry field `reused_on`:** a registry entry may carry the date another company took its ticker. A trade in that ticker before that date resolves to the entry (`delisted`), ahead of rule 3. With the user's approval, STRR.D23 was registered (`reused_on` 2025-09-05, `asof` 2023-05-05). Its 8 executions and 6 fee lines were re-keyed to `us:strr.d23:xnas:2026` after a backup (`journal-before-strr-rekey-20261023T084502Z.sqlite3`).
  - **Not automated:** the price check is a manual maintenance step, because it needs bar requests for every instrument (about 2,500).

## Alternatives considered

- **Instrument IDs per company instead of per ticker:** the right long-term identity, but every stored bar, drawing and layout keys on ticker IDs today.
- **Alpaca's OTC feed for prints after a delisting:** a separate subscription. On 2026-10-22 one read-only request (`feed=otc`, daily bars) with the user's key was refused: 403 "subscription does not permit querying OTC data".

## Revisit when

- Ticker reuse turns up in the user's trades, or IDs move to per-company identity.
- OTC data is wanted after a delisting.
