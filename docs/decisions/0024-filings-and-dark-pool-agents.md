# ADR 0024 — Filings and dark pool evidence Agents (Phase 6)

- **Status:** accepted (sources approved by the user on 2026-10-21)
- **Date:** 2026-10-21

## Context

SYSTEM_SPEC names News, News Cycle and Dark Pool Agents whose data must keep source and time provenance. The roadmap's Phase 6A asks for an SEC EDGAR adapter that respects SEC fair-access rules, and a FINRA weekly ATS/non-ATS adapter whose output is labelled as delayed weekly aggregates, not intraday dark-pool activity.

Spec §12.4 adds a point-in-time rule: publication time and the time this system first observed a record are kept apart. A retrospective download does not prove a record was available at its publication time.

ADR 0022 built the first Agent: deterministic local evidence, optional Claude commentary on request, read-only, stored in `agents.sqlite3`. These two Agents follow the same design. Claude commentary stays available in code but deferred: nothing here calls it.

## Decision

### Sources (official, free, documented only)

| Source                 | Endpoint                                                                                                        | Access                                                                                                                    | Limits we apply                                   |
| ---------------------- | --------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------- |
| SEC EDGAR ticker map   | `https://www.sec.gov/files/company_tickers.json`                                                                | No login. Declared `User-Agent` with a contact.                                                                           | Cached for 1 day.                                 |
| SEC EDGAR Submissions  | `https://data.sec.gov/submissions/CIK##########.json` (+ older `files[]` pages only when the window needs them) | Same.                                                                                                                     | At most 5 req/s (SEC allows 10).                  |
| SEC EDGAR CompanyFacts | `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json`                                                 | Same.                                                                                                                     | Same; cached per CIK.                             |
| FINRA OTC Transparency | `https://api.finra.org/data/group/otcMarket/name/weeklySummary`                                                 | FINRA's API needs an OAuth client credential. A free **Individual** account can issue a **Public** credential ($0/month). | FINRA allows 1,200 sync req/min; we use far less. |

- **SEC User-Agent:** read from `SIW_SEC_USER_AGENT` (for example `SIW research you@example.com`). Without it, SEC requests are refused locally (`409 sec_user_agent_missing`) and nothing is sent. It is not a secret, but the repository is public, so no contact address is committed.
- **FINRA credential:** a new `finra` entry on the Keys screen (client ID and client secret), kept in the OS keychain like the other keys. The bearer token is held in memory only. Without a credential, the Dark Pool Agent answers `409 finra_credential_missing` and makes no call.
- **Not used:** news sites, issuer IR pages, paid feeds, any login other than the FINRA API credential above, HTML scraping, and the otctransparency.finra.org web screens. A news crawler needs its own per-domain review (spec §12.4) and a later ADR.
- **US only:** EDGAR and FINRA cover US issuers. A `kr:` instrument is refused with an explanation (DART would be a later, separate source).

### Local cache and provenance

- **Store:** `SIW_DATA_DIR/public_sources.sqlite3`, separate from market data and agents.
- **Per record:** `source_id` (`sec-edgar-submissions`, `sec-edgar-companyfacts`, `finra-otc-weekly`), `source_record_id` (accession number; FINRA symbol + week + summary type), `published_at` (see below), `first_observed_utc` (first time we stored it, never changed), `last_retrieved_utc`, the SHA-256 of the raw payload, the parser version and the parsed record.
- **Revisions:** if a parsed record changes (a FINRA correction, a restated XBRL value), the new version is stored next to the old one, never over it. An analysis uses the latest revision this system had observed by the cutoff when there is one, otherwise the latest revision. Records export as the project's `source_record` contract; `available_at_utc` is never claimed, because it was not observed.
- **Refetch:** a request re-downloads a source when its cached copy is older than 6 hours, or when the cutoff is later than the last download and that download is more than 5 minutes old. SEC's ticker map is refreshed once a day, and older submissions pages once. If a download fails, the cached copy is used and the analysis says from when; with no cache, the request fails (`502 public_source_unavailable`). Tests never touch the network (HTTP is injected and mocked).

### Publication time and point-in-time rules

- **SEC filing:** `published_at` = EDGAR's acceptance time, to the second, in UTC. A filing counts as known at a cutoff only if accepted at or before it.
  - **Time zone quirk (verified 2026-10-21):** the Submissions JSON writes `acceptanceDateTime` with a `Z`, but against each filing's own header (`ACCEPTANCE-DATETIME`, US/Eastern) it is late by the Eastern UTC offset: 4 hours in summer, 5 in winter (4 AAPL filings checked, both seasons). The parser subtracts that offset. After the correction, all 1,001 AAPL filings fall inside EDGAR's 06:00–22:00 ET hours; read literally as UTC, 279 would not.
- **SEC XBRL fact:** a fact is used only if its accession number matches a filing accepted at or before the cutoff. When that match fails, the `filed` date (day only) must be strictly before the cutoff's US/Eastern date.
- **FINRA week:** `published_at` = `initialPublishedDate` (FINRA gives a date only; it is stored as 00:00 US/Eastern of that day). FINRA's publication lag is two weeks for NMS Tier 1 and four weeks for Tier 2 and OTC securities. Because the time of day is unknown, a week is used only if it was published **before** the cutoff's US/Eastern date. If its `lastUpdateDate` is on or after that date, the analysis says the value shown may include later corrections.
- **Observed vs published:** when `first_observed_utc` is after `published_at` (always true for history we backfill), the analysis states that availability at publication time is reconstructed from the source's own dates, not observed by us.
- **Hindsight:** filings accepted after the cutoff appear only if the request asks for them (`after_days`, 0–30, default 0), and then each one is flagged `hindsight: true`.

### Filings Agent (`agent_id: filings_review`)

Target: an instrument and a cutoff (from a chart point or a trade date), with a look-back window (`window_days`, default 30, at most 365).

Facts:

- The filings accepted in the window: form, accepted time, how many days before the cutoff, and for 8-K/8-K/A their item codes with short names (2.02 results, 5.02 officer changes, 1.01 material agreements and the rest of the SEC item list).
- Counts by form in the window (8-K, 10-Q, 10-K, S-1/S-3, 4, SC 13D/G, DEF 14A).
- The latest 10-Q and 10-K known at the cutoff: accepted time, period of report and days since.
- From CompanyFacts, the latest known quarterly revenue and diluted EPS at the cutoff, with the change against the same quarter a year earlier, when both exist under the same concept and unit.
- Links: the EDGAR filing index URL for each filing, as `source_refs` (accession numbers).

No filing text is downloaded or summarised; only the structured metadata above. Classification is limited to SEC's own form types and item codes.

### Dark Pool Agent (`agent_id: dark_pool_review`)

Target: an instrument and a cutoff, with a history length (`weeks`, default 26, at most 104).

Facts (all from FINRA symbol-level summaries `ATS_W_SMBL` and `OTC_W_SMBL`):

- The latest week known at the cutoff: week start date, published date, ATS shares and trades, non-ATS OTC shares, and the ATS share of off-exchange volume (ATS ÷ (ATS + non-ATS)).
- The same week against the history: ratio to the mean and the percentile of ATS shares and of the ATS share over the previous published weeks.
- Average trade size on ATS (shares ÷ trades) against its history.
- **Share of total volume:** ATS shares ÷ the week's total volume from local daily bars, **only** when those bars come from a consolidated feed (`source_feed_scope` such as `alpaca-sip`). For IEX-only or unknown feeds the fact is left out and listed in `missing`, because IEX volume is a small slice of the market.
- Always present in `uncertainty`: "FINRA weekly OTC/ATS data is delayed aggregate volume. It shows neither direction (buy or sell), nor prices, nor intraday timing, and it is not live dark-pool prints."

### Records, contract and API

- **Store:** analyses go to the existing `agents.sqlite3` through `services/agents/store.py`. The store takes the agent ID, version and indicator versions from the evidence instead of hard-coding the review Agent's.
- **Contract:** `agent_analysis.target.type` gains `filings` and `dark_pool`, with optional `window_days`, `after_days` and `weeks`. Facts keep the existing units (counts, percents, ratios, currency, days, text). Fixtures and generated TypeScript types are updated.
- **Endpoints:** `POST /v1/agents/review/filings` and `POST /v1/agents/review/dark-pool` build and store an analysis. The existing list, read and commentary endpoints serve them unchanged.
- **Screen:** two sections in the Agent panel (English and Korean), using the chart's instrument and cutoff, and a reminder that FINRA data is weekly, delayed and directionless.
- **Writes nothing elsewhere:** no journal, ledger or order access; nothing returned can place an order.

## Alternatives considered

- **EDGAR full-text search or RSS feeds:** full-text search is a different, less documented service; RSS covers recent filings only. Submissions JSON covers the history we need with precise acceptance times.
- **otctransparency.finra.org downloads without a credential:** the same data, but through web screens rather than the documented API; that is scraping.
- **Paid dark-pool or news feeds (ChartExchange, Unusual Whales, news APIs):** registered as candidates only; no purchase without the user's approval.
- **Downloading and summarising filing text:** more context, but large downloads, parser risk and claims that cannot be checked against structured facts. Deferred until a commentary-driven need appears.

## Revisit when

- A news crawler or a licensed news feed is approved (News and News Cycle Agents).
- Korean disclosures (DART) are wanted.
- A consolidated-volume source becomes the default, so share-of-total-volume can always be computed.
- SEC or FINRA change access rules, limits or field names.
