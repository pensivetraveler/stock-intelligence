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
  type ReactNode,
} from "react";

import { ChartSyncGroup } from "./chart-sync";
import { ChartPanel } from "./ChartPanel";
import { useI18n, type MessageKey, type Translate } from "./i18n";
import {
  AppMark,
  CandlesIcon,
  ChevronDownIcon,
  CrosshairIcon,
  FibIcon,
  FitIcon,
  HorizontalLinesIcon,
  ImportIcon,
  IndicatorsIcon,
  JournalIcon,
  KeyIcon,
  LayoutIcon,
  LogScaleIcon,
  MagnetIcon,
  ReplayIcon,
  RulerIcon,
  SearchIcon,
  SingleLayoutIcon,
  TextIcon,
  TrashIcon,
  TrendLineIcon,
  UndoIcon,
  WatchlistIcon,
  ZoomIcon,
} from "./icons";
import { ImportPanel } from "./ImportPanel";
import { JournalPanel } from "./JournalPanel";
import { Watchlist } from "./Watchlist";
import {
  MarketApiError,
  TIMEFRAMES,
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
  type SessionScope,
  type WorkspaceState,
} from "./workspace-state";

interface WorkspaceProps {
  api: MarketApi;
  storage?: Storage | null;
  newId?: () => string;
  /** The key management screen, shown in the right panel when given. */
  keysPanel?: ReactNode;
}

type Panel = "watchlist" | "journal" | "import" | "keys";

/** Per-chart view options that are not part of the saved layout. */
interface ChartTools {
  magnet: boolean;
  logScale: boolean;
  fitRequest: number;
}

const SPLIT_STORAGE_KEY = "siw.chartSplit";
const NO_TOOLS: ChartTools = { magnet: false, logScale: false, fitRequest: 0 };

function shiftDate(date: string, days: number): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

const CLOCK = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

/** Current exchange time, like the clock in a terminal's bottom bar. */
function ExchangeClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, []);
  return <span className="exchange-clock">{CLOCK.format(now)} ET</span>;
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
  keysPanel,
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
  const [search, setSearch] = useState<string | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [panel, setPanel] = useState<Panel | null>("watchlist");
  const [tools, setTools] = useState<[ChartTools, ChartTools]>([
    NO_TOOLS,
    NO_TOOLS,
  ]);
  const [split, setSplitState] = useState(() => {
    try {
      return storage?.getItem(SPLIT_STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const setSplit = (value: boolean) => {
    setSplitState(value);
    try {
      storage?.setItem(SPLIT_STORAGE_KEY, value ? "1" : "0");
    } catch {
      // Not saved: the layout still applies until the page reloads.
    }
  };
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

  const activeConfig = layout.charts[activeChart];
  const updateActive = (changes: Partial<ChartConfig>) =>
    updateChart(activeChart)({ ...activeConfig, ...changes });
  const activeSymbol = symbolOf(activeConfig.instrumentId);
  const matches = instruments.filter((item) =>
    item.instrument.symbol
      .toLowerCase()
      .includes((search ?? "").trim().toLowerCase()),
  );
  const chooseInstrument = (instrumentId: string) => {
    setSearch(null);
    updateActive({ instrumentId });
  };
  const visibleCharts = split ? [0, 1] : [activeChart];
  const setChartTool = (patch: Partial<ChartTools>) =>
    setTools((previous) => {
      const next: [ChartTools, ChartTools] = [...previous];
      next[activeChart] = { ...next[activeChart], ...patch };
      return next;
    });
  const tool = tools[activeChart];
  const comingSoon = (stage: number) => t("tb.comingSoon", { stage });

  return (
    <div className={`terminal-shell${panel ? " with-panel" : ""}`}>
      <header className="top-bar" role="toolbar" aria-label={t("tb.label")}>
        <span className="brand" title="Stock Intelligence Workstation">
          <AppMark />
        </span>
        <div className="symbol-search">
          <button
            type="button"
            className="tb-button symbol-button"
            aria-label={t("chart.instrument")}
            aria-expanded={search !== null}
            onClick={() => setSearch(search === null ? "" : null)}
          >
            <SearchIcon />
            {activeSymbol}
          </button>
          {search !== null ? (
            <div
              className="popover symbol-popover"
              role="dialog"
              aria-label={t("chart.searchSymbol")}
            >
              <input
                autoFocus
                value={search}
                placeholder={t("chart.searchSymbol")}
                aria-label={t("chart.searchSymbol")}
                onChange={(event) => setSearch(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Escape") setSearch(null);
                  if (event.key === "Enter" && matches[0])
                    chooseInstrument(matches[0].instrument.instrument_id);
                }}
              />
              <ul>
                {matches.map((item) => (
                  <li key={item.instrument.instrument_id}>
                    <button
                      type="button"
                      onClick={() =>
                        chooseInstrument(item.instrument.instrument_id)
                      }
                    >
                      <strong>{item.instrument.symbol}</strong>
                      <small>
                        {item.instrument.venue} · {item.source_id}
                      </small>
                    </button>
                  </li>
                ))}
                {matches.length === 0 ? (
                  <li className="symbol-empty">{t("chart.noMatch")}</li>
                ) : null}
              </ul>
            </div>
          ) : null}
        </div>
        <span className="tb-divider" aria-hidden="true" />
        <div
          className="tb-group"
          role="group"
          aria-label={t("chart.timeframe")}
        >
          {TIMEFRAMES.map((value) => (
            <button
              key={value}
              type="button"
              className="tb-button"
              aria-pressed={value === activeConfig.timeframe}
              onClick={() => updateActive({ timeframe: value })}
            >
              {value}
            </button>
          ))}
        </div>
        <span className="tb-divider" aria-hidden="true" />
        <button
          type="button"
          className="tb-button"
          aria-label={t("tb.candles")}
          title={t("tb.candles")}
          aria-pressed="true"
        >
          <CandlesIcon />
        </button>
        <button
          type="button"
          className="tb-button"
          disabled
          title={comingSoon(2)}
        >
          <IndicatorsIcon />
          {t("tb.indicators")}
        </button>
        <button
          type="button"
          className="tb-button"
          aria-label={t("tb.splitLayout")}
          title={t("tb.splitLayout")}
          aria-pressed={split}
          onClick={() => setSplit(!split)}
        >
          {split ? <LayoutIcon /> : <SingleLayoutIcon />}
        </button>
        <button
          type="button"
          className="tb-button"
          disabled
          title={comingSoon(4)}
        >
          <ReplayIcon />
          {t("tb.replay")}
        </button>
        <span className="tb-divider" aria-hidden="true" />
        <button
          type="button"
          className="tb-button"
          aria-label={t("ws.undo")}
          title={t("ws.undo")}
          disabled={undoStack.length === 0}
          onClick={undo}
        >
          <UndoIcon />
        </button>
        <span className="tb-spacer" />
        <div className="workspace-menu">
          <button
            type="button"
            className="tb-button"
            aria-expanded={menuOpen}
            aria-label={t("ws.menu", { name: active.name })}
            onClick={() => setMenuOpen(!menuOpen)}
          >
            {active.name}
            <ChevronDownIcon />
          </button>
          {menuOpen ? (
            <div className="popover workspace-popover">
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
                    setNameForm({
                      mode: "rename",
                      value: active.name,
                      error: null,
                    })
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
                  onClick={() =>
                    changeWorkspace(removeWorkspace(library, active.id))
                  }
                >
                  {t("ws.delete")}
                </button>
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
                    {nameForm.mode === "save-as"
                      ? t("ws.newName")
                      : t("ws.name")}
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
                    {nameForm.mode === "save-as"
                      ? t("ws.save")
                      : t("ws.saveName")}
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
            </div>
          ) : null}
        </div>
      </header>

      <aside
        className="left-tools"
        role="toolbar"
        aria-label={t("chart.tools")}
        aria-orientation="vertical"
      >
        <button
          type="button"
          aria-pressed="true"
          aria-label={t("tools.crosshair")}
          title={t("tools.crosshair")}
        >
          <CrosshairIcon />
        </button>
        {(
          [
            ["tools.trendLine", TrendLineIcon],
            ["tools.horizontalLine", HorizontalLinesIcon],
            ["tools.fib", FibIcon],
            ["tools.text", TextIcon],
            ["tools.measure", RulerIcon],
            ["tools.zoom", ZoomIcon],
          ] as const
        ).map(([key, ToolIcon]) => (
          <button
            key={key}
            type="button"
            disabled
            aria-label={t(key)}
            title={`${t(key)} — ${comingSoon(3)}`}
          >
            <ToolIcon />
          </button>
        ))}
        <span className="tool-divider" aria-hidden="true" />
        <button
          type="button"
          aria-pressed={tool.magnet}
          aria-label={t("chart.magnet")}
          title={t("chart.magnet")}
          onClick={() => setChartTool({ magnet: !tool.magnet })}
        >
          <MagnetIcon />
        </button>
        <button
          type="button"
          aria-pressed={tool.logScale}
          aria-label={t("chart.logScale")}
          title={t("chart.logScale")}
          onClick={() => setChartTool({ logScale: !tool.logScale })}
        >
          <LogScaleIcon />
        </button>
        <button
          type="button"
          aria-label={t("chart.fit")}
          title={t("chart.fit")}
          onClick={() => setChartTool({ fitRequest: tool.fitRequest + 1 })}
        >
          <FitIcon />
        </button>
        <span className="tool-divider" aria-hidden="true" />
        <button
          type="button"
          disabled
          aria-label={t("tools.removeDrawings")}
          title={`${t("tools.removeDrawings")} — ${comingSoon(3)}`}
        >
          <TrashIcon />
        </button>
      </aside>

      <main className="chart-area">
        {instrumentsError ? (
          <p className="chart-error banner" role="alert">
            {t("ws.instrumentsUnavailable", { error: instrumentsError })}
          </p>
        ) : null}
        <div className={`chart-grid${split ? " split" : ""}`}>
          {visibleCharts.map((index) => (
            <ChartPanel
              key={index}
              active={split && index === activeChart}
              onActivate={() => setActiveChart(index as 0 | 1)}
              chartId={`chart-${index + 1}`}
              title={t("ws.chartTitle", { n: index + 1 })}
              api={api}
              config={layout.charts[index]}
              instruments={instruments}
              instrumentsReady={instrumentsReady}
              sync={sync}
              journal={journal}
              onJournalChange={refreshJournal}
              magnet={tools[index].magnet}
              logScale={tools[index].logScale}
              fitRequest={tools[index].fitRequest}
            />
          ))}
        </div>
        <footer className="range-bar" aria-label={t("tb.sessionBar")}>
          <div className="date-stepper">
            <button
              type="button"
              className="tb-button"
              aria-label={t("chart.previousDay")}
              onClick={() =>
                updateActive({
                  sessionDate: shiftDate(activeConfig.sessionDate, -1),
                })
              }
            >
              ‹
            </button>
            <input
              type="date"
              aria-label={t("chart.sessionDate")}
              value={activeConfig.sessionDate}
              onChange={(event) =>
                event.target.value &&
                updateActive({ sessionDate: event.target.value })
              }
            />
            <button
              type="button"
              className="tb-button"
              aria-label={t("chart.nextDay")}
              onClick={() =>
                updateActive({
                  sessionDate: shiftDate(activeConfig.sessionDate, 1),
                })
              }
            >
              ›
            </button>
          </div>
          <div className="tb-group" role="group" aria-label={t("chart.hours")}>
            {(["regular", "extended"] as SessionScope[]).map((value) => (
              <button
                key={value}
                type="button"
                className="tb-button"
                aria-pressed={value === activeConfig.sessionScope}
                onClick={() => updateActive({ sessionScope: value })}
              >
                {t(value === "regular" ? "chart.regular" : "chart.extended")}
              </button>
            ))}
          </div>
          <span className="tb-spacer" />
          <ExchangeClock />
        </footer>
      </main>

      {panel ? (
        <aside
          className={`side-panel side-panel-${panel}`}
          aria-label={t(`panel.${panel}`)}
        >
          {panel === "watchlist" ? (
            <Watchlist
              api={api}
              instruments={instruments}
              sessionDate={activeConfig.sessionDate}
              activeInstrumentId={activeConfig.instrumentId}
              onPick={(instrumentId) => updateActive({ instrumentId })}
            />
          ) : null}
          {panel === "journal" ? (
            <JournalPanel
              manual={ledgers.manual}
              imported={ledgers.imported}
              scenarios={ledgers.scenarios}
              error={journalError}
              symbolOf={symbolOf}
            />
          ) : null}
          {panel === "import" ? (
            journalError ? (
              <p className="workspace-note" role="status">
                {journalError}
              </p>
            ) : (
              <ImportPanel api={api} onImported={refreshJournal} />
            )
          ) : null}
          {panel === "keys" ? keysPanel : null}
        </aside>
      ) : null}

      <nav className="right-rail" aria-label={t("panel.rail")}>
        {(
          [
            ["watchlist", WatchlistIcon],
            ["journal", JournalIcon],
            ["import", ImportIcon],
            ...(keysPanel ? ([["keys", KeyIcon]] as const) : []),
          ] as const
        ).map(([id, RailIcon]) => (
          <button
            key={id}
            type="button"
            aria-label={t(`panel.${id}`)}
            title={t(`panel.${id}`)}
            aria-pressed={panel === id}
            onClick={() => setPanel(panel === id ? null : id)}
          >
            <RailIcon />
          </button>
        ))}
      </nav>
    </div>
  );
}
