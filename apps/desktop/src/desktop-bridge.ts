/** What the Electron shell (apps/electron/preload.cjs) exposes to SIW Research. */
interface SiwDesktop {
  /** Opens SIW Agent, the separate app for paper trading (ADR 0036). */
  openAgentApp(): Promise<{ opened: boolean; reason: string | null }>;
  /** Calls back when the app menu's "설정…" is chosen; returns an unsubscribe. */
  onShowSettings?(callback: () => void): () => void;
}

/** The shell's bridge, or null in a browser (where the button is hidden). */
export function desktopBridge(): SiwDesktop | null {
  const bridge = (window as { siwDesktop?: SiwDesktop }).siwDesktop;
  return bridge && typeof bridge.openAgentApp === "function" ? bridge : null;
}
