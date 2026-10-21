# ADR 0022 — Chart and trade review Agent (Phase 5)

- **Status:** accepted
- **Date:** 2026-10-21

## Context

SYSTEM_SPEC asks for evidence-grounded Agents:

- An Agent's reasoning is kept apart from its evidence. Each analysis records the data, indicator versions, the point-in-time cutoff and the uncertainty.
- Agents give analysis, never execution authority, and keep facts, inferences, hypotheses and simulated outcomes apart.
- Agent output can never write confirmed fills.
- A candle can be clicked to "ask agent", and Agents may critique backtest runs.

The roadmap's Phase 5 asks for one end-to-end, read-only Agent workflow before any others.

On 2026-10-21 the user chose:

- local evidence plus commentary from Claude;
- three review targets: real trades, backtest runs and a point on a chart.

## Decision

### Two layers

1. **Evidence (local, deterministic, always produced):** the API computes facts from the same bars, indicators and ledgers the screens use. Each fact has a `code`, a `value` with a unit, an English `text`, and a `hindsight` flag for facts known only after the moment under review. The evidence is stored, and the same inputs give the same facts.
2. **Commentary (Claude, optional, on request):** only when the user presses "Ask Claude" and a Claude API key is registered on the Keys screen.
   - **What is sent:** only the evidence facts and a short description of the target are sent to the Anthropic Messages API. No account numbers, keys or ledger IDs are sent, and the screen shows the exact payload before sending.
   - **What comes back:** Claude answers in JSON: a summary, inferences, hypotheses, questions for the user and uncertainty. Every claim must rest on the facts given. The answer is stored with the model name and time.
   - **Re-asking:** asking again adds another commentary and never changes the evidence.

### Targets

- **Chart point:** an instrument, a timeframe and a cutoff (the end of the clicked candle).
  - **No later bars:** bars are read with `as_of_utc` set to the cutoff, so nothing after it is used.
  - **Facts:** last close and change, position in the 20-bar range, SMA 20/50 and slope, RSI 14, ATR as a percentage of price, volume against its 20-bar average, VWAP distance for intraday bars, and the number of bars used. Facts that need more bars than are available are left out and said so.
- **Real trades:** chosen executions of one instrument from the manual, imported and broker API ledgers.
  - **Trade facts:** quantities, average prices and FIFO realized P&L, fees, the holding period and any quantity still open.
  - **Each fill:** where it sat in that session's daily range (0 = low, 1 = high).
  - **At entry:** RSI and the SMA position, computed from daily bars of the sessions before the entry (cutoff: the entry session's open).
  - **Hindsight (flagged):** the most favourable and most adverse excursion from the average entry while held, and the close 1 and 5 sessions after the last exit.
  - **Imprecise times:** an execution without an exact time is placed by its date only, which is noted in the uncertainty.
- **Backtest run:** facts from the stored, pinned results:
  - return against buy-and-hold and maximum drawdown;
  - trade count, with fewer than 30 closed trades flagged as weak evidence;
  - the share of profit from the single best trade;
  - per-symbol contribution;
  - cost drag (fees plus slippage as a share of gross profit);
  - unrealized P&L at the end;
  - rejected orders by reason;
  - the run's own warnings (raw prices, suspected splits and the rest).

### Records and API

- **Storage:** `SIW_DATA_DIR/agents.sqlite3` keeps every analysis, immutable apart from appended commentaries.
- **Contract:** `agent_analysis` holds the target, the facts, sources (execution IDs, the run ID, dataset revisions, indicator versions), the cutoff, the uncertainty and the commentaries. It also embeds a standard `agent_evidence` document (observations = fact texts; inferences = the latest commentary's inferences).
- **Endpoints:**
  - `POST /v1/agents/review/chart`, `POST /v1/agents/review/trades` and `POST /v1/agents/review/backtest` build and store an analysis.
  - `POST /v1/agents/analyses/{id}/commentary` asks Claude.
  - `GET /v1/agents/analyses` and `GET /v1/agents/analyses/{id}` read them back.
- **Writes nothing elsewhere:** the Agent never writes to the journal or any ledger, and nothing it returns can place an order.

### Claude

- **Key:** a new `anthropic` entry on the Keys screen (one API key, not per account), kept in the OS keychain like the other keys.
- **Model:** `claude-sonnet-5-5` by default, overridable with `SIW_AGENT_MODEL`. The request has a 60-second timeout and a `max_tokens` cap.
- **Not advice:** the system prompt forbids buy and sell recommendations, price targets and certainty beyond the facts, and asks for the user's language. The screen shows that the commentary is analysis, not investment advice.
- **Code:** this Agent generates no code, so nothing it says can be executed. Agent-generated strategy code (spec §6.7) remains for a later Agent and must be approved first.

## Alternatives considered

- **Claude only:** the user could not review anything without a key, and nothing would be reproducible.
- **Local rules only:** no natural-language commentary, which the user wanted.
- **Sending raw bars or ledgers to Claude:** more context, but more data leaves the machine, and claims could no longer be checked against a fixed list of facts.

## Revisit when

- News, News Cycle or Dark Pool Agents need a shared evidence store.
- Commentary should run without a click, or in batches.
- Agent-generated strategies or indicators are added (approval flow).
