# ADR 0020 — Korean listings: calendar, instrument IDs, KRW and market routing (stage K1)

- **Status:** accepted (implemented; checked live with Toss for Samsung Electronics 005930)
- **Date:** 2026-10-20

## Context

The user asked for full chart support for Korean stocks. Until now the code assumed the US market in its calendar, New York dates, USD and the `us:` instrument IDs.

On 2026-10-20 the user decided:

- **Sessions:** the regular session is KRX 09:00–15:30 KST. The extended sessions are NXT pre-market 08:00–09:00 and after-market 15:30–20:00, shown with the same regular/extended toggle as US stocks.
- **Delivery:** three PRs. K1 is the foundation (this ADR), K2 the screen, K3 the ledgers and realtime.

Toss is the only Korean source. Findings from the live check on 2026-10-20:

- Minute candles cover 08:00–20:00 KST with KRX and NXT trading combined: 720 bars for 005930, with timestamps marking each bar's end.
- Daily candles are stamped at midnight KST and cover the whole combined day: their open, high, low and close equal the 08:00–20:00 minute data, not the 09:00–15:30 regular session.
- The KRX calendar is available in `exchange_calendars` as XKRX for 2000–2030, including late openings such as 10:00 on the first trading day of the year. NXT is not.

## Decision

- **Calendar:** `UsEquityCalendar` becomes `ExchangeCalendar`; the old name stays as an alias. Each market has a profile: time zone, extended hours and policy version.
  - US behavior is unchanged, including the `xnys-regular-…` calendar version, so cached US data keeps its revisions.
  - Korea uses XKRX; KOSDAQ (XKOS) follows the same KRX schedule. The policy version is `kr-equity-nxt-extended-hours-v1-2026-10-20`.
  - Pre-market opens at 08:00 KST and after-market closes at 20:00 KST.
- **Instrument IDs:**
  - Korean listings: `kr:<6-character KRX code>:<xkrx|xkos>:2026`, with XKRX for KOSPI and XKOS for KOSDAQ.
  - `InstrumentRef` now allows `kr_equity` and `KRW`.
  - `services/market_data/instruments.py` parses IDs and returns each instrument's market and calendar.
- **Calendar per instrument:**
  - Bars record the calendar actually used (previously a literal `XNAS`).
  - Aggregation runs on the instrument's own calendar.
  - Stored bars are partitioned by the instrument's exchange date, so Korean bars are filed under KST dates.
  - The session list API accepts XKRX and XKOS.
- **Routing:** `MarketRouter` sends Korean IDs to Toss and everything else to the configured provider, which may stay Alpaca.
  - The Korean source is built lazily from the first enabled real-account Toss key. It shares that key's single session (ADR 0017) and is cached like any provider when `SIW_DATA_DIR` is set.
  - If no Toss key is registered, a Korean request returns a clear error.
- **Toss Korean adapter:** the same adapter with `market="KR"`.
  - Source `toss-kr` with feed scope `toss-kr-krx-nxt-integrated`. The feed scope states that KRX and NXT trading are combined.
  - Prices in KRW and the KRX calendar; all three sessions are covered.
- **Whole-day daily bars:** a new bar session value, `all_day`.
  - Toss's Korean daily and weekly bars span 08:00–20:00 KST and are labeled `all_day`.
  - US daily bars stay `regular`.
  - Daily and weekly paths include a session only when the bar's whole span lies inside the requested range.

## Live check (2026-10-20, 005930)

- **Minute bars:** 720 of 720 expected minutes (pre 60, regular 390, post 270), complete.
- **30-minute bars:** 24, complete.
- **Daily bars:** 31, complete, and labeled `all_day`. The 10/20 bar equals the 08:00–20:00 minute data (open 222,500, high 223,000, low 208,000, close 209,000).
  - The regular-session close at 15:30 was 211,000, which shows why the label matters.
  - The daily volume was 27,184,733, about 2.6% above the sum of minute bars (26,482,933).
- **Weekly bars:** 8, `all_day`.

## Consequences

- Korean regular-session bars contain KRX and NXT trading combined, since Toss does not separate them; the feed scope says so.
- The weekly series of a market follows its daily span: Korean weeks run Monday 08:00 to Friday 20:00 KST.
- The screen does not show Korean time or KRW formatting yet (K2), and the ledgers and realtime stream are US-only until K3.

## Revisit when

- Toss offers KRX-only candles, or a second Korean source appears: a KRX-only regular series would then be possible.
- NXT changes its hours: a new policy version would be needed.
- `exchange_calendars` misses a KRX special day such as the delayed opening on the college entrance exam (CSAT) day: verify it against Toss's market calendar.
