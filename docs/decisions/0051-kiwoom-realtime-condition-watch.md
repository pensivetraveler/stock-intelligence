# ADR 0051 — Recording SIW_당일급등주 in realtime

- **Status:** accepted. The user asked to keep the SIW_당일급등주 list from premarket to the end of the aftermarket (2026-11-05), first as a five-minute poll, then chose Kiwoom's realtime condition search instead.
- **Date:** 2026-11-05
- **Amends:** ADR 0048 ("Nothing is stored"): this one condition is now recorded.

## Context

ADR 0048 runs a connected condition once when asked and stores nothing. The user wants to see later which tickers the condition held during the day.

Kiwoom's published limits ([REST API intro page](https://openapi.kiwoom.com/intro), read 2026-11-05) apply per account (token):

- US queries: 5 per second, or 3 per second from 09:00 to 10:00 KST;
- one WebSocket session;
- 10 realtime US conditions per session;
- at most 100 tickers in a condition result.

The page states no separate limit for a condition query and no date for the figures. A five-minute poll would stay far below these limits. A faster one would run into them, and it would still miss what happens between two polls.

usa20290 (`GCNSRREQ` with `search_type` `1`) answers the condition's tickers (`jmcode`, `stex_tp`) and then keeps sending `REAL` messages. Each message holds `S2` items whose values carry:

- `9001`, the ticker;
- `843`, `I` when the ticker enters and `D` when it leaves;
- `20`, a time as `HHMMSS`;
- `841` and `907`, which are undocumented. `841` equals the condition's `seq` in the spec example.

usa20291 (`GCNSRCLR`) stops the events. The spec says the condition list must be read before subscribing.

**Live check on 2026-11-05, 15:08–15:18 KST (01:08 ET), read-only:**

- The subscription to SIW_당일급등주 (seq 2) answered 38 tickers.
- Kiwoom sent a `PING` every 10 seconds.
- No enter or leave event came in those ten minutes.
- `GCNSRCLR` was answered with `trnm` `GCNSRCLR` and "정상종료되었습니다".
- During the subscription, a second process issued a token, opened a second connection, ran SIW_전일급등주 once (36 rows) and closed. The first connection kept receiving `PING`s.
- Whether its events kept coming after the second connection could not be seen, since no event came at all.

The time zone of field `20` is not documented.

## Decision

- **One condition, in realtime.**
  - `siw-collector watch-condition` watches SIW_당일급등주 (`--name` can pick another connected condition) from premarket open to aftermarket close of each US session.
  - The window is 04:00–20:00 New York time, or 04:00–17:00 on an early close. Holidays are skipped, using the project's exchange calendar (XNYS).
  - It is a separate collector process, not part of the API, so it keeps running while the apps are closed.
- **A connection per watch.**
  - Each watch logs in, looks the condition's `seq` up by name (ADR 0048), subscribes, and records.
  - When the window closes or the process is stopped, it sends `GCNSRCLR` and closes.
  - A lost connection, or a minute without any message (`PING`s included), ends the watch. A new one starts after 5 s, doubling to at most 5 min, and back to 5 s after a watch that lasted 5 min.
  - A name that is not saved, or saved twice, stops the collector, because retrying cannot fix it.
- **What is stored** (`SIW_DATA_DIR/screeners/kiwoom_condition_watch.sqlite3`, append-only):
  - each watch, with its condition name and `seq`, start, end and why it ended;
  - the list the subscription answered (`listed`);
  - every `enter`/`leave` event, with the time SIW received it, Kiwoom's field `20` as given, and all of the event's raw values.
  - A watch whose end was never recorded (killed process, sleeping machine) is closed as `interrupted` at its last event the next time the store opens. A gap is therefore on record and never reads as a quiet market.
- **Times:** the received time (UTC) is the only time SIW vouches for. Kiwoom's field `20` is kept as text until its time zone is checked.
- **Reading it back:** `siw-collector export-condition --date YYYY-MM-DD` prints the watches and events of one New York date as JSON. The record is a log of what Kiwoom reported. It is not a bar or evidence, and replay, backtests and agents do not read it. Using it in a strategy needs its own decision.
- **Allowlist:** the socket can now also send `GCNSRREQ` with `search_type` `1` and `GCNSRCLR`. Nothing else is added.

## Alternatives

- **Polling every five minutes, or faster:** this misses tickers that enter and leave between polls. Below a few seconds it needs a kept-open connection and would approach the query limit, for which Kiwoom states no figure for condition searches.
- **Running the watch inside the API process:** the record would stop whenever the Research app is closed.
- **Combining the realtime events with a five-minute `search_type` `0` snapshot (prices and volume):** this would make the record check itself. Whether a second request on the subscribed connection disturbs the subscription is not known, so it is left for later.

## Not verified

- the shape of a real `REAL`/`S2` event, the time zone of field `20`, and what `841` and `907` mean;
- whether a second connection with the same key, such as the Research app's one-off run, silently ends the realtime events, which Kiwoom's one-session limit suggests it may;
- whether the 100-ticker limit applies to the realtime list;
- whether Kiwoom resends the list or the events after a reconnect without a new subscription.

## Revisit when

- a live event shows a different shape, or field `20` turns out to be New York or Seoul time;
- events stop while a second connection is open: then the one-off run must share the collector's connection or pause it;
- the user wants other conditions watched, a screen in the app, or the record used by a strategy.
