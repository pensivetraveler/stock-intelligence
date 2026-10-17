import type {
  ImportedLedgerResponse,
  ManualLedgerResponse,
  ScenarioLedgerResponse,
} from "@siw/contracts/types";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";

import { ChartSyncGroup } from "./chart-sync";
import { ChartPanel } from "./ChartPanel";
import { ImportPanel } from "./ImportPanel";
import { JournalPanel } from "./JournalPanel";
import {
  MarketApiError,
  type InstrumentListing,
  type MarketApi,
} from "./market-api";
import {
  activeWorkspace,
  applyTemplate,
  LAYOUT_TEMPLATES,
  loadLibrary,
  removeWorkspace,
  renameWorkspace,
  saveAs,
  saveLibrary,
  switchTo,
  updateActiveLayout,
  validateName,
  type WorkspaceLibrary,
} from "./workspace-library";
import {
  DEFAULT_WORKSPACE,
  type ChartConfig,
  type WorkspaceState,
} from "./workspace-state";

interface WorkspaceProps {
  api: MarketApi;
  storage?: Storage | null;
  newId?: () => string;
}

const UNDO_LIMIT = 20;

function browserStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function randomId(): string {
  return crypto.randomUUID();
}

type NameForm = {
  mode: "save-as" | "rename";
  value: string;
  error: string | null;
} | null;

export function Workspace({
  api,
  storage = browserStorage(),
  newId = randomId,
}: WorkspaceProps) {
  const [library, setLibrary] = useState<WorkspaceLibrary>(() =>
    loadLibrary(storage, newId),
  );
  // Layouts replaced by a template or reset in this workspace, newest last.
  const [undoStack, setUndoStack] = useState<WorkspaceState[]>([]);
  const [templateId, setTemplateId] = useState(LAYOUT_TEMPLATES[0].id);
  const [nameForm, setNameForm] = useState<NameForm>(null);
  const [instruments, setInstruments] = useState<InstrumentListing[]>([]);
  const [instrumentsError, setInstrumentsError] = useState<string | null>(null);
  const [ledgers, setLedgers] = useState<{
    manual: ManualLedgerResponse | null;
    imported: ImportedLedgerResponse | null;
    scenarios: ScenarioLedgerResponse | null;
    error: string | null;
  }>({ manual: null, imported: null, scenarios: null, error: null });
  const active = activeWorkspace(library);
  const layout = active.layout;
  const [sync] = useState(() => new ChartSyncGroup(layout.syncTimeRange));

  useEffect(() => {
    sync.enabled = layout.syncTimeRange;
  }, [sync, layout.syncTimeRange]);

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

  useEffect(() => saveLibrary(storage, library), [storage, library]);

  const refreshJournal = useCallback(() => {
    Promise.all([
      api.manualLedger(),
      api.importedLedger(),
      api.scenarioLedger(),
    ])
      .then(([manual, imported, scenarios]) =>
        setLedgers({ manual, imported, scenarios, error: null }),
      )
      .catch((error: unknown) =>
        setLedgers({
          manual: null,
          imported: null,
          scenarios: null,
          error:
            error instanceof MarketApiError &&
            error.code === "journal_storage_unconfigured"
              ? "Trade journal is off: start the API with SIW_DATA_DIR to record executions and scenarios."
              : `Trade journal unavailable: ${error instanceof Error ? error.message : "request failed"}`,
        }),
      );
  }, [api]);

  useEffect(() => refreshJournal(), [refreshJournal]);

  const journal = useMemo(
    () => ({
      manual: ledgers.manual?.executions ?? [],
      imported: ledgers.imported?.executions ?? [],
      scenarios:
        ledgers.scenarios?.scenarios.map((item) => item.scenario) ?? [],
    }),
    [ledgers],
  );
  const symbolOf = (instrumentId: string) =>
    instruments.find((item) => item.instrument.instrument_id === instrumentId)
      ?.instrument.symbol ?? instrumentId;

  const setLayout = (update: (previous: WorkspaceState) => WorkspaceState) =>
    setLibrary((previous) =>
      updateActiveLayout(previous, update(activeWorkspace(previous).layout)),
    );

  const replaceLayout = (next: WorkspaceState) => {
    setUndoStack((stack) => [...stack, layout].slice(-UNDO_LIMIT));
    setLayout(() => next);
  };

  const undo = () => {
    const previous = undoStack.at(-1);
    if (previous) {
      setUndoStack((stack) => stack.slice(0, -1));
      setLayout(() => previous);
    }
  };

  const updateChart = (index: 0 | 1) => (config: ChartConfig) =>
    setLayout((previous) => {
      const charts: [ChartConfig, ChartConfig] = [...previous.charts];
      charts[index] = config;
      return { ...previous, charts };
    });

  const changeWorkspace = (next: WorkspaceLibrary) => {
    setUndoStack([]);
    setNameForm(null);
    setLibrary(next);
  };

  const submitName = (event: FormEvent) => {
    event.preventDefault();
    if (!nameForm) return;
    const taken = library.workspaces
      .filter((item) => nameForm.mode === "save-as" || item.id !== active.id)
      .map((item) => item.name);
    const error = validateName(nameForm.value, taken);
    if (error) {
      setNameForm({ ...nameForm, error });
      return;
    }
    if (nameForm.mode === "save-as") {
      changeWorkspace(saveAs(library, nameForm.value, newId()));
    } else {
      setLibrary(renameWorkspace(library, active.id, nameForm.value));
      setNameForm(null);
    }
  };

  const template =
    LAYOUT_TEMPLATES.find((item) => item.id === templateId) ??
    LAYOUT_TEMPLATES[0];

  return (
    <div className="workspace">
      <div
        className="workspace-bar"
        role="toolbar"
        aria-label="Workspace controls"
      >
        <label className="inline-field">
          Workspace
          <select
            value={active.id}
            onChange={(event) =>
              changeWorkspace(switchTo(library, event.target.value))
            }
          >
            {library.workspaces.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="secondary"
          onClick={() =>
            setNameForm({ mode: "save-as", value: "", error: null })
          }
        >
          Save as…
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() =>
            setNameForm({ mode: "rename", value: active.name, error: null })
          }
        >
          Rename
        </button>
        <button
          type="button"
          className="secondary"
          disabled={library.workspaces.length === 1}
          title={
            library.workspaces.length === 1
              ? "The last workspace cannot be deleted"
              : undefined
          }
          onClick={() => changeWorkspace(removeWorkspace(library, active.id))}
        >
          Delete
        </button>
        <span className="toolbar-divider" aria-hidden="true" />
        <label className="inline-field">
          Layout template
          <select
            value={templateId}
            onChange={(event) =>
              setTemplateId(event.target.value as typeof templateId)
            }
          >
            {LAYOUT_TEMPLATES.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="secondary"
          title={template.description}
          onClick={() => replaceLayout(applyTemplate(layout, template))}
        >
          Apply to both charts
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => replaceLayout(DEFAULT_WORKSPACE)}
        >
          Reset layout
        </button>
        <button
          type="button"
          className="secondary"
          disabled={undoStack.length === 0}
          onClick={undo}
        >
          Undo
        </button>
        <label className="toggle">
          <input
            type="checkbox"
            checked={layout.syncTimeRange}
            onChange={(event) =>
              setLayout((previous) => ({
                ...previous,
                syncTimeRange: event.target.checked,
              }))
            }
          />
          Sync visible time range
        </label>
      </div>

      {nameForm ? (
        <form className="name-form" onSubmit={submitName}>
          <label className="inline-field">
            {nameForm.mode === "save-as"
              ? "New workspace name"
              : "Workspace name"}
            <input
              autoFocus
              value={nameForm.value}
              maxLength={80}
              aria-invalid={nameForm.error !== null}
              onChange={(event) =>
                setNameForm({
                  ...nameForm,
                  value: event.target.value,
                  error: null,
                })
              }
            />
          </label>
          <button type="submit">
            {nameForm.mode === "save-as" ? "Save" : "Save name"}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => setNameForm(null)}
          >
            Cancel
          </button>
          {nameForm.error ? (
            <span className="chart-error" role="alert">
              {nameForm.error}
            </span>
          ) : null}
        </form>
      ) : null}

      <p className="workspace-note">
        {template.description}. Templates copy settings into each chart; later
        changes stay per chart. Synthetic data · times in ET · workspaces saved
        on this device.
      </p>
      {instrumentsError ? (
        <p className="chart-error" role="alert">
          Instruments unavailable: {instrumentsError}
        </p>
      ) : null}
      <div className="chart-grid">
        {layout.charts.map((config, index) => (
          <ChartPanel
            key={index}
            chartId={`chart-${index + 1}`}
            title={`Chart ${index + 1}`}
            api={api}
            config={config}
            instruments={instruments}
            sync={sync}
            onChange={updateChart(index as 0 | 1)}
            journal={journal}
            onJournalChange={refreshJournal}
          />
        ))}
      </div>
      <JournalPanel
        manual={ledgers.manual}
        imported={ledgers.imported}
        scenarios={ledgers.scenarios}
        error={ledgers.error}
        symbolOf={symbolOf}
      />
      {ledgers.error ? null : (
        <ImportPanel api={api} onImported={refreshJournal} />
      )}
    </div>
  );
}
