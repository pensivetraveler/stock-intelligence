import type { RealtimeEvent } from "@siw/contracts/types";

export type TradeEvent = Extract<RealtimeEvent, { event: "trade" }>;
export type OrderbookEvent = Extract<RealtimeEvent, { event: "orderbook" }>;

/** The app's relay of the broker stream (see /v1/realtime). */
export interface RealtimeSocket {
  /** Stocks whose trades and order book this screen wants. */
  setSymbols(symbols: string[]): void;
  close(): void;
}

/** Live data kept for one symbol, newest first. */
export interface LiveSymbol {
  trades: TradeEvent[];
  orderbook: OrderbookEvent | null;
}

export const LIVE_TRADES = 30;

/**
 * Open the relay, resubscribing after every reconnect (backoff up to 30 s). Events that
 * are not realtime events are ignored.
 */
export function openRealtime(
  url: string,
  onEvent: (event: RealtimeEvent) => void,
  createSocket: (url: string) => WebSocket = (address) =>
    new WebSocket(address),
): RealtimeSocket {
  let socket: WebSocket | null = null;
  let symbols: string[] = [];
  let closed = false;
  let attempt = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const send = () => {
    if (socket?.readyState === WebSocket.OPEN) {
      socket.send(JSON.stringify({ symbols }));
    }
  };
  const connect = () => {
    socket = createSocket(url);
    socket.onopen = () => {
      attempt = 0;
      send();
    };
    socket.onmessage = (message: MessageEvent<string>) => {
      try {
        const event = JSON.parse(message.data) as { kind?: string };
        if (event.kind === "realtime_event") onEvent(event as RealtimeEvent);
      } catch {
        // Not JSON: ignore.
      }
    };
    socket.onclose = () => {
      if (closed) return;
      timer = setTimeout(connect, Math.min(30_000, 1_000 * 2 ** attempt));
      attempt += 1;
    };
  };
  connect();
  return {
    setSymbols(next) {
      symbols = next;
      send();
    },
    close() {
      closed = true;
      if (timer !== undefined) clearTimeout(timer);
      socket?.close();
    },
  };
}

/** The live state of one symbol after an event. */
export function applyLive(
  previous: Record<string, LiveSymbol>,
  event: TradeEvent | OrderbookEvent,
): Record<string, LiveSymbol> {
  const current = previous[event.symbol] ?? { trades: [], orderbook: null };
  const next =
    event.event === "trade"
      ? { ...current, trades: [event, ...current.trades].slice(0, LIVE_TRADES) }
      : { ...current, orderbook: event };
  return { ...previous, [event.symbol]: next };
}
