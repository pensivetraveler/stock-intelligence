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
import { useI18n, type MessageKey, type Translate } from "./i18n";
import { ImportPanel } from "./ImportPanel";
import { JournalPanel } from "./JournalPanel";
import { Watchlist } from "./Watchlist";
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

// validateName's messages, mapped to dictionary keys for display.
function nameError(t: Translate, error: string): string {
  const keys: Record<string, MessageKey> = {
    "Enter a name": "name.empty",
    "A workspace with this name already exists": "name.taken",
  };
  if (error in keys) return t(keys[error]);
  const tooLong = /^Use at most (\d+) characters$/.exec(error);
  return tooLong ? t("name.tooLong", { max: tooLong[1] }) : error;
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
  const { t } = useI18n();
  const [library, setLibrary] = useState<WorkspaceLibrary>(() =>
    loadLibrary(storage, newId),
  );
  // Layouts replaced by a template or reset in this workspace, newest last.
  const [undoStack, setUndoStack] = useState<WorkspaceState[]>([]);
  const [templateId, setTemplateId] = useState(LAYOUT_TEMPLATES[0].id);
  const [nameForm, setNameForm] = useState<NameForm>(null);
  const [activeChart, setActiveChart] = useState<0 | 1>(0);
  const [instruments, setInstruments] = useState<InstrumentListing[]>([]);
  const [instrumentsError, setInstrumentsError] = useState<string | null>(null);
  const [instrumentsReady, setInstrumentsReady] = useState(false);
  const [ledgers, setLedgers] = useState<{
    manual: ManualLedgerResponse | null;
    imported: ImportedLedgerResponse | null;
    scenarios: ScenarioLedgerResponse | null;
    // Kept raw and worded at render time, in the current language.
    error: { cause: unknown } | null;
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
        if (current) {
          setInstruments(items);
          setInstrumentsReady(true);
        }
      })
      .catch((error: unknown) => {
        if (current) {
          setInstrumentsReady(true);
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
          error: { cause: error },
        }),
      );
  }, [api]);

  useEffect(() => refreshJournal(), [refreshJournal]);

  const journalError =
    ledgers.error === null
      ? null
      : ledgers.error.cause instanceof MarketApiError &&
          ledgers.error.cause.code === "journal_storage_unconfigured"
        ? t("ws.journalOff")
        : t("ws.journalUnavailable", {
            error:
              ledgers.error.cause instanceof Error
                ? ledgers.error.cause.message
                : t("common.requestFailed"),
          });

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
        aria-label={t("ws.controls")}
      >
        <label className="inline-field">
          {t("ws.workspace")}
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
          {t("ws.saveAs")}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() =>
            setNameForm({ mode: "rename", value: active.name, error: null })
          }
        >
          {t("ws.rename")}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={library.workspaces.length === 1}
          title={
            library.workspaces.length === 1
              ? t("ws.lastCannotDelete")
              : undefined
          }
          onClick={() => changeWorkspace(removeWorkspace(library, active.id))}
        >
          {t("ws.delete")}
        </button>
        <span className="toolbar-divider" aria-hidden="true" />
        <label className="inline-field">
          {t("ws.template")}
          <select
            value={templateId}
            onChange={(event) =>
              setTemplateId(event.target.value as typeof templateId)
            }
          >
            {LAYOUT_TEMPLATES.map((item) => (
              <option key={item.id} value={item.id}>
                {t(`template.${item.id}.name`)}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="secondary"
          title={t(`template.${template.id}.description`)}
          onClick={() => replaceLayout(applyTemplate(layout, template))}
        >
          {t("ws.applyBoth")}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => replaceLayout(DEFAULT_WORKSPACE)}
        >
          {t("ws.reset")}
        </button>
        <button
          type="button"
          className="secondary"
          disabled={undoStack.length === 0}
          onClick={undo}
        >
          {t("ws.undo")}
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
          {t("ws.sync")}
        </label>
      </div>

      {nameForm ? (
        <form className="name-form" onSubmit={submitName}>
          <label className="inline-field">
            {nameForm.mode === "save-as" ? t("ws.newName") : t("ws.name")}
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
            {nameForm.mode === "save-as" ? t("ws.save") : t("ws.saveName")}
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => setNameForm(null)}
          >
            {t("ws.cancel")}
          </button>
          {nameForm.error ? (
            <span className="chart-error" role="alert">
              {nameError(t, nameForm.error)}
            </span>
          ) : null}
        </form>
      ) : null}

      <p className="workspace-note">
        {t("ws.note", {
          description: t(`template.${template.id}.description`),
        })}
      </p>
      {instrumentsError ? (
        <p className="chart-error" role="alert">
          {t("ws.instrumentsUnavailable", { error: instrumentsError })}
        </p>
      ) : null}
      <div className="terminal">
        <div className="chart-grid">
          {layout.charts.map((config, index) => (
            <ChartPanel
              key={index}
              active={index === activeChart}
              onActivate={() => setActiveChart(index as 0 | 1)}
              chartId={`chart-${index + 1}`}
              title={t("ws.chartTitle", { n: index + 1 })}
              api={api}
              config={config}
              instruments={instruments}
              instrumentsReady={instrumentsReady}
              sync={sync}
              onChange={updateChart(index as 0 | 1)}
              journal={journal}
              onJournalChange={refreshJournal}
            />
          ))}
        </div>
        <Watchlist
          api={api}
          instruments={instruments}
          sessionDate={layout.charts[activeChart].sessionDate}
          activeInstrumentId={layout.charts[activeChart].instrumentId}
          onPick={(instrumentId) =>
            updateChart(activeChart)({
              ...layout.charts[activeChart],
              instrumentId,
            })
          }
        />
      </div>
      <JournalPanel
        manual={ledgers.manual}
        imported={ledgers.imported}
        scenarios={ledgers.scenarios}
        error={journalError}
        symbolOf={symbolOf}
      />
      {journalError ? null : (
        <ImportPanel api={api} onImported={refreshJournal} />
      )}
    </div>
  );
}
