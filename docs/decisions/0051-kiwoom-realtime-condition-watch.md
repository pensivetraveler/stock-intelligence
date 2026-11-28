# ADR 0051 — Recording SIW_당일급등주 in realtime

- **Status:** accepted. The user asked to keep the SIW_당일급등주 list from premarket to the end of the aftermarket (2026-11-05), first as a five-minute poll, then chose Kiwoom's realtime condition search instead.
- **Date:** 2026-11-05. Amended the same day after a live run: subscriptions are renewed every 30 minutes (see "Live run on 2026-11-05, evening").
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

**Live run on 2026-11-05, evening (15:51–18:51 KST), read-only:**

- One subscription, opened at 15:51 KST, stayed connected until 18:40 KST and kept receiving `PING`s. It received no enter or leave event at all, also after the 18:00 KST premarket open.
- At 18:40 KST a one-off run of the condition answered 6 tickers. Of the 38 tickers on the list when the subscription opened, 37 had left, and 5 new ones had entered. None of these changes was reported. Kiwoom probably turns the condition over to the new day at the premarket open and leaves older subscriptions silent; this is a guess.
- A fresh subscription at 18:41 KST received 18 events in two minutes (CDT entering and leaving), shaped as above: `841` was the condition's `seq` (2), `907` was always `2`, and `20` was **Seoul time** (`184121` for an event received at 09:41:21 UTC).
- While a later subscription was open, a one-off run on a second connection made Kiwoom close the first connection (`ConnectionClosedOK`). An earlier subscription that Kiwoom closed with `1000 Bye` after 30 minutes was most likely ended the same way.

## Decision

- **One condition, in realtime.**
  - `siw-collector watch-condition` watches SIW_당일급등주 (`--name` can pick another connected condition) from premarket open to aftermarket close of each US session.
  - The window is 04:00–20:00 New York time, or 04:00–17:00 on an early close. Holidays are skipped, using the project's exchange calendar (XNYS).
  - It is a separate collector process, not part of the API, so it keeps running while the apps are closed.
- **A connection per watch.**
  - Each watch logs in, looks the condition's `seq` up by name (ADR 0048), subscribes, and records.
  - When the window closes or the process is stopped, it sends `GCNSRCLR` and closes.
  - A subscription is renewed every 30 minutes: the watch ends as `renewed` and a new one opens at once with a fresh list. This bounds a silent subscription like the one seen live to 30 minutes.
  - The first subscription of a window opens one minute after the premarket open, not at it.
  - A lost connection, or a minute without any message (`PING`s included), ends the watch. A new one starts after 5 s, doubling to at most 5 min, and back to 5 s after a watch that lasted 5 min.
  - A name that is not saved, or saved twice, stops the collector, because retrying cannot fix it.
- **What is stored** (`SIW_DATA_DIR/screeners/kiwoom_condition_watch.sqlite3`, append-only):
  - each watch, with its condition name and `seq`, start, end and why it ended;
  - the list the subscription answered (`listed`);
  - every `enter`/`leave` event, with the time SIW received it, Kiwoom's field `20` as given, and all of the event's raw values.
  - since 2026-11-28 (mondash #393, ADR 0055), Kiwoom's quote of each entering ticker (`entry_quotes`): usa20100 (미국주식 현재가 종목정보) is asked on a worker thread right after the `enter` event, with the event's exchange code, and every field of the reply is kept as sent (current price, open, high, low, previous close, `acc_trde_qty`, `mac` in thousand USD and the rest), with when it was asked and answered. A quote that failed (or found 500 already waiting, `queue_full`) is kept as an `error` row. Leaves and listed tickers are not quoted. The export adds it to the event as `entry_quote`.
  - A watch whose end was never recorded (killed process, sleeping machine) is closed as `interrupted` at its last event the next time the store opens. A gap is therefore on record and never reads as a quiet market.
- **Missed changes are shown:** the export gives each watch `differs_from_previous`, the tickers on its fresh list that the previous watch of the same condition did not end with (`entered`), and those it ended with that are missing (`left`). These are changes no event reported, whether during a silent subscription, a reconnect or the night between two windows. The first watch has `null`.
- **Times:** the received time (UTC) is the only time SIW vouches for. Kiwoom's field `20` is Seoul time as `HHMMSS` without a date; it is kept as text.
- **Reading it back:** `siw-collector export-condition --date YYYY-MM-DD` prints the watches and events of one New York date as JSON. The record is a log of what Kiwoom reported. It is not a bar or evidence, and replay, backtests and agents do not read it. Using it in a strategy needs its own decision.
- **Allowlist:** the socket can now also send `GCNSRREQ` with `search_type` `1` and `GCNSRCLR`. The entry quote uses its own REST client that can only call `POST /api/us/mrkcond` (usa20100), with the session's existing token, on a separate connection from the subscription. Nothing else is added.

## Alternatives

- **Subscribing only once, a minute after the premarket open:** simpler, but a subscription that turns silent at another moment (the regular open, the aftermarket) would go unnoticed for the rest of the day.
- **Polling every five minutes, or faster:** this misses tickers that enter and leave between polls. Below a few seconds it needs a kept-open connection and would approach the query limit, for which Kiwoom states no figure for condition searches.
- **Running the watch inside the API process:** the record would stop whenever the Research app is closed.
- **Combining the realtime events with a five-minute `search_type` `0` snapshot (prices and volume):** this would make the record check itself. Whether a second request on the subscribed connection disturbs the subscription is not known, so it is left for later.

## Record check on 2026-11-25 (read-only)

The collector's own store (`data/screeners/kiwoom_condition_watch.sqlite3`, opened `mode=ro`) held 312 watches of SIW_당일급등주 from 2026-11-09 to 2026-11-24 (ten sessions; 11-18 and 11-19 are missing because the Mac was off, 11-17 has no aftermarket), with 6,561 enter, 6,141 leave and 8,849 listed rows.

- **`907`:** `2` in all 12,702 enter and leave events, as is `841` (the condition's `seq`). The Kiwoom spec (usa20290) shows `907` only in its example, also as `2`, and does not describe it. A field that never changes cannot say why a ticker entered.
- **The new day:** every session's first watch (one minute after 04:00 ET) received events, from the first session on (11-09: 194 events before 09:30 ET).
- **The regular open:** the watch spanning 09:30 ET received events after 09:30 on all nine sessions that had one (11 to 55 events each).
- **Silent subscriptions during the day:** of 276 renewals, 267 had a fresh list equal to where the previous watch ended. The other nine differed by one ticker each. In eight, that ticker had an event within 26 s of the boundary on one side or the other (a flickering ticker caught by the switch), so no subscription went silent. One (AIXI, 11-13 09:31 ET) was on the list for the whole previous watch with no event and missing from the next list: one leave was not reported, or it left during the switch. Differences after a lost connection (seven) or overnight are expected and shown by `differs_from_previous`.
- **100-ticker limit:** the largest list a subscription answered had 68 tickers, so the limit was never reached.
- **Resending after a reconnect:** not needed, since every reconnect opens a new subscription and records its fresh list.

## Not verified

- what `907` means (it is always `2`; see above);
- whether the 100-ticker limit applies to the realtime list (largest list so far: 68);
- whether a subscription can turn silent at a moment not yet seen; the 30-minute renewal bounds it, and the records so far show no such silence after 11-05.

## Revisit when

- a live event shows a different shape, or field `20` turns out to be New York or Seoul time;
- the one-off runs of the Research app end the collector's connection too often: a second connection closes the first, so they must then share the collector's connection or pause it;
- records show changes in `differs_from_previous` often enough that 30 minutes is too long, or never, so it can be longer;
- the user wants other conditions watched, a screen in the app, or the record used by a strategy.
