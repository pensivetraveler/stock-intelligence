import { useEffect, useState } from "react";

import { ChartSyncGroup } from "./chart-sync";
import { ChartPanel } from "./ChartPanel";
import type { InstrumentListing, MarketApi } from "./market-api";
import {
  DEFAULT_WORKSPACE,
  loadWorkspace,
  saveWorkspace,
  type ChartConfig,
  type WorkspaceState,
} from "./workspace-state";

interface WorkspaceProps {
  api: MarketApi;
  storage?: Storage | null;
}

function browserStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function Workspace({ api, storage = browserStorage() }: WorkspaceProps) {
  const [state, setState] = useState<WorkspaceState>(() =>
    loadWorkspace(storage),
  );
  const [instruments, setInstruments] = useState<InstrumentListing[]>([]);
  const [instrumentsError, setInstrumentsError] = useState<string | null>(null);
  const [sync] = useState(() => new ChartSyncGroup(state.syncTimeRange));

  useEffect(() => {
    sync.enabled = state.syncTimeRange;
  }, [sync, state.syncTimeRange]);

  useEffect(() => {
    let current = true;
    api
      .instruments()
      .then((items) => {
        if (current) setInstruments(items);
      })
      .catch((error: unknown) => {
        if (current) {
          setInstrumentsError(
            error instanceof Error ? error.message : "Request failed",
          );
        }
      });
    return () => {
      current = false;
    };
  }, [api]);

  useEffect(() => saveWorkspace(storage, state), [storage, state]);

  const updateChart = (index: 0 | 1) => (config: ChartConfig) =>
    setState((previous) => {
      const charts: [ChartConfig, ChartConfig] = [...previous.charts];
      charts[index] = config;
      return { ...previous, charts };
    });

  return (
    <div className="workspace">
      <div className="workspace-bar">
        <label className="toggle">
          <input
            type="checkbox"
            checked={state.syncTimeRange}
            onChange={(event) =>
              setState((previous) => ({
                ...previous,
                syncTimeRange: event.target.checked,
              }))
            }
          />
          Sync visible time range
        </label>
        <button
          type="button"
          className="secondary"
          onClick={() => setState(DEFAULT_WORKSPACE)}
        >
          Reset layout
        </button>
        <span className="workspace-note">
          Synthetic data · times in ET · layout saved on this device
        </span>
      </div>
      {instrumentsError ? (
        <p className="chart-error" role="alert">
          Instruments unavailable: {instrumentsError}
        </p>
      ) : null}
      <div className="chart-grid">
        {state.charts.map((config, index) => (
          <ChartPanel
            key={index}
            chartId={`chart-${index + 1}`}
            title={`Chart ${index + 1}`}
            api={api}
            config={config}
            instruments={instruments}
            sync={sync}
            onChange={updateChart(index as 0 | 1)}
          />
        ))}
      </div>
    </div>
  );
}
