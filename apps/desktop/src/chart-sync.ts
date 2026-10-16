export interface TimeRange {
  from: number;
  to: number;
}

type Listener = (range: TimeRange) => void;

/**
 * Shares the visible time range between charts while enabled. A chart that
 * receives a range does not echo it back, so two charts cannot loop forever.
 */
export class ChartSyncGroup {
  private readonly listeners = new Map<string, Listener>();
  private publishing = false;
  enabled: boolean;

  constructor(enabled = true) {
    this.enabled = enabled;
  }

  register(chartId: string, listener: Listener): () => void {
    this.listeners.set(chartId, listener);
    return () => {
      if (this.listeners.get(chartId) === listener) {
        this.listeners.delete(chartId);
      }
    };
  }

  publish(sourceId: string, range: TimeRange): void {
    if (!this.enabled || this.publishing) {
      return;
    }
    this.publishing = true;
    try {
      for (const [chartId, listener] of this.listeners) {
        if (chartId !== sourceId) {
          listener(range);
        }
      }
    } finally {
      this.publishing = false;
    }
  }
}
