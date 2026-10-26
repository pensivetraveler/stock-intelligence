import type {
  BacktestFill,
  CustomIndicator,
  BrokerLedgerResponse,
  BrokerOrderExecution,
  ImportedLedgerResponse,
  ManualLedgerResponse,
  ScenarioLedgerResponse,
} from "@siw/contracts/types";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

import { ChartSyncGroup } from "./chart-sync";
import { ChartPanel } from "./ChartPanel";
import { FloatingWindow, type WindowGeometry } from "./FloatingWindow";
import type { DrawingTool } from "./drawings";
import { IndicatorDialog } from "./IndicatorDialog";
import { TimeframePicker } from "./TimeframePicker";
import {
  isDailyTimeframe,
  loadFavorites,
  normalizeFavorites,
  saveFavorites,
  timeframeSeconds,
} from "./timeframes";
import {
  REPLAY_OFF,
  REPLAY_SPEEDS,
  stepBack,
  stepForward,
  type ReplayState,
} from "./replay";
import { defaultParams, type OhlcvBar } from "./indicators";
import { useI18n, type MessageKey, type Translate } from "./i18n";
import {
  AppMark,
  CandlesIcon,
  ChevronDownIcon,
  CloseIcon,
  DockIcon,
  CrosshairIcon,
  FibExtensionIcon,
  FibIcon,
  FitIcon,
  GridLayoutIcon,
  HorizontalLinesIcon,
  IndicatorsIcon,
  AgentIcon,
  BacktestIcon,
  JournalIcon,
  TradeReviewIcon,
  KeyIcon,
  QuoteIcon,
  InfoIcon,
  ResearchIcon,
  AccountIcon,
  LayoutIcon,
  LogScaleIcon,
  MagnetIcon,
  PopOutIcon,
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
import {
  loadAddedInstruments,
  mergeInstruments,
  saveAddedInstruments,
} from "./added-instruments";
import { ImportPanel } from "./ImportPanel";
import {
  KR_ZONE,
  todayIn,
  US_ZONE,
  zoneFormat,
  zoneOf,
  type MarketZone,
} from "./market-zone";
import { QuotePanel } from "./QuotePanel";
import { applyLive, type LiveSymbol, type RealtimeSocket } from "./realtime";
import { AccountPanel } from "./AccountPanel";
import { ResearchPanel } from "./ResearchPanel";
import { AgentPanel } from "./AgentPanel";
import type { AgentApi, ChartReviewRequest } from "./agent-api";
import { BacktestScreen } from "./BacktestScreen";
import { TradeReviewScreen } from "./TradeReviewScreen";
import type { CustomIndicatorApi } from "./custom-indicator-api";
import { latestCustom, registerCustom } from "./custom-indicators";
import { CustomIndicatorEditor } from "./CustomIndicatorEditor";
import { FormulaDialog } from "./FormulaEditor";
import { PineDialog } from "./PineEditor";
import { pineDefinition, pineWidths } from "./pine";
import type { BacktestApi } from "./backtest-api";
import type { ReviewApi } from "./review-api";
import { StockInfoPanel } from "./StockInfoPanel";
import { JournalPanel } from "./JournalPanel";
import { ReconciliationView } from "./ReconciliationView";
import { useSessionQuotes } from "./session-quotes";
import { Watchlist } from "./Watchlist";
import {
  MarketApiError,
  type InstrumentListing,
  type MarketApi,
  type SymbolSearchResult,
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
  CHART_SLOTS,
  DEFAULT_WORKSPACE,
  type ChartConfig,
  type LayoutMode,
  type SessionScope,
  type WorkspaceState,
} from "./workspace-state";

interface WorkspaceProps {
  api: MarketApi;
  /** Open every chart on today at its exchange, or the last trading day before it. */
  openOnToday?: boolean;
  storage?: Storage | null;
  newId?: () => string;
  /** The key management screen, shown in the right panel when given. */
  keysPanel?: ReactNode;
  /** The backtest screen's client; without one the screen is not offered. */
  backtestApi?: BacktestApi;
  /** The review Agent's client; without one it is not offered. */
  agentApi?: AgentApi;
  /** The trade review screen's client (ADR 0042); without one it is not offered. */
  reviewApi?: ReviewApi;
  /** Python indicators' client (ADR 0023); without one they are not offered. */
  customApi?: CustomIndicatorApi;
}

type Panel =
  | "watchlist"
  | "quote"
  | "info"
  | "research"
  | "account"
  | "journal"
  | "keys"
  | "backtest"
  | "tradeReview"
  | "review";

/** Per-chart view options that are not part of the saved layout. */
interface ChartTools {
  magnet: boolean;
  logScale: boolean;
  fitRequest: number;
}

const SPLIT_STORAGE_KEY = "siw.chartSplit";
const WATCHLIST_WINDOW_KEY = "siw.watchlistWindow";
const DEFAULT_WATCHLIST_WINDOW: WindowGeometry = {
  x: 120,
  y: 90,
  width: 420,
  height: 320,
};

/** Saved floating watchlist: whether it is open and where. */
function loadWatchlistWindow(storage: Storage | null | undefined): {
  open: boolean;
  geometry: WindowGeometry;
} {
  try {
    const saved = JSON.parse(storage?.getItem(WATCHLIST_WINDOW_KEY) ?? "null");
    const geometry = saved?.geometry;
    if (
      typeof saved?.open === "boolean" &&
      ["x", "y", "width", "height"].every(
        (name) => typeof geometry?.[name] === "number",
      )
    ) {
      return { open: saved.open, geometry };
    }
  } catch {
    // Unreadable or unavailable storage: start docked.
  }
  return { open: false, geometry: DEFAULT_WATCHLIST_WINDOW };
}
const NO_TOOLS: ChartTools = { magnet: false, logScale: false, fitRequest: 0 };

const REPLAY_CLOCK: Intl.DateTimeFormatOptions = {
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
};

function replayTimeText(time: number, zone: MarketZone): string {
  const text = zoneFormat(zone, "en-US", REPLAY_CLOCK).format(
    new Date(time * 1000),
  );
  return `${text} ${zone.label}`;
}

function shiftDate(date: string, days: number): string {
  const value = new Date(`${date}T00:00:00Z`);
  value.setUTCDate(value.getUTCDate() + days);
  return value.toISOString().slice(0, 10);
}

const CLOCK: Intl.DateTimeFormatOptions = {
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
};

/** Current exchange time, like the clock in a terminal's bottom bar. */
/** Current time at the active chart's exchange (ET or KST). */
function ExchangeClock({ zone }: { zone: MarketZone }) {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1_000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <span className="exchange-clock">
      {zoneFormat(zone, "en-US", CLOCK).format(now)} {zone.label}
    </span>
  );
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

/** Newest broker executions listed in the journal panel. */
const JOURNAL_BROKER_ROWS = 500;

export function Workspace({
  api,
  storage = browserStorage(),
  newId = randomId,
  keysPanel,
  backtestApi,
  agentApi,
  reviewApi,
  customApi,
  openOnToday = false,
}: WorkspaceProps) {
  const { t } = useI18n();
  const [library, setLibrary] = useState<WorkspaceLibrary>(() => {
    const loaded = loadLibrary(storage, newId);
    if (!openOnToday) return loaded;
    // Every chart opens on today at its own exchange (New York for US listings).
    return {
      ...loaded,
      workspaces: loaded.workspaces.map((item) => ({
        ...item,
        layout: {
          ...item.layout,
          charts: item.layout.charts.map((chart) => ({
            ...chart,
            sessionDate: todayIn(zoneOf(chart.instrumentId)),
          })),
        },
      })),
    };
  });
  // Layouts replaced by a template or reset in this workspace, newest last.
  const [undoStack, setUndoStack] = useState<WorkspaceState[]>([]);
  const [templateId, setTemplateId] = useState(LAYOUT_TEMPLATES[0].id);
  const [nameForm, setNameForm] = useState<NameForm>(null);
  const [activeChart, setActiveChart] = useState(0);
  const [search, setSearch] = useState<string | null>(null);
  // Ticker search across all US-listed stocks, answered by the API's symbol directory.
  const [remote, setRemote] = useState<{
    query: string;
    results: SymbolSearchResult[];
    stale: boolean;
    error: unknown;
  } | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [indicatorsOpen, setIndicatorsOpen] = useState(false);
  const [drawTool, setDrawTool] = useState<DrawingTool>("cursor");
  const [favorites, setFavorites] = useState(() => loadFavorites(storage));
  const toggleFavorite = (timeframe: string) =>
    setFavorites((current) => {
      const next = normalizeFavorites(
        current.includes(timeframe)
          ? current.filter((item) => item !== timeframe)
          : [...current, timeframe],
      );
      saveFavorites(storage, next);
      return next;
    });
  const [replay, setReplay] = useState<ReplayState>(REPLAY_OFF);
  const replayOn = replay.phase !== "off";
  // End times of each chart's first and last loaded bar (replay stepping limits).
  const [bounds, setBounds] = useState<
    { first: number | null; last: number | null }[]
  >(() =>
    Array.from({ length: CHART_SLOTS }, () => ({ first: null, last: null })),
  );
  const [onBounds] = useState(() =>
    Array.from({ length: CHART_SLOTS }, (_, index) => index).map(
      (index) => (first: number | null, last: number | null) =>
        setBounds((previous) => {
          if (
            previous[index].first === first &&
            previous[index].last === last
          ) {
            return previous;
          }
          const next = [...previous];
          next[index] = { first, last };
          return next;
        }),
    ),
  );

  useEffect(() => {
    if (drawTool === "cursor") return;
    const cancel = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawTool("cursor");
    };
    window.addEventListener("keydown", cancel);
    return () => window.removeEventListener("keydown", cancel);
  }, [drawTool]);
  // A watchlist reopened floating after a reload is not also shown in the side panel.
  const [panel, setPanel] = useState<Panel | null>(() =>
    loadWatchlistWindow(storage).open ? null : "watchlist",
  );
  const [tools, setTools] = useState<ChartTools[]>(() =>
    Array.from({ length: CHART_SLOTS }, () => NO_TOOLS),
  );
  // Before layout modes were saved with the workspace, a two-chart choice lived in
  // its own key; it still decides the mode of layouts saved without one.
  const [legacySplit] = useState(() => {
    try {
      return storage?.getItem(SPLIT_STORAGE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const [floating, setFloatingState] = useState(() =>
    loadWatchlistWindow(storage),
  );
  const setFloating = (next: { open: boolean; geometry: WindowGeometry }) => {
    setFloatingState(next);
    try {
      storage?.setItem(WATCHLIST_WINDOW_KEY, JSON.stringify(next));
    } catch {
      // Not saved: the window still works until the page reloads.
    }
  };
  const popOutWatchlist = () => {
    setFloating({ ...floating, open: true });
    if (panel === "watchlist") setPanel(null);
  };
  const dockWatchlist = () => {
    setFloating({ ...floating, open: false });
    setPanel("watchlist");
  };
  // Whether each chart finished loading; watchlist quotes wait for the visible ones.
  const [chartSettled, setChartSettled] = useState<boolean[]>(() =>
    Array.from({ length: CHART_SLOTS }, () => false),
  );
  const [onChartSettled] = useState(() =>
    Array.from({ length: CHART_SLOTS }, (_, index) => index).map(
      (index) => (settled: boolean) =>
        setChartSettled((previous) => {
          if (previous[index] === settled) return previous;
          const next = [...previous];
          next[index] = settled;
          return next;
        }),
    ),
  );
  const [configuredInstruments, setInstruments] = useState<InstrumentListing[]>(
    [],
  );
  const [added, setAdded] = useState<InstrumentListing[]>(() =>
    loadAddedInstruments(storage),
  );
  const instruments = useMemo(
    () => mergeInstruments(configuredInstruments, added),
    [configuredInstruments, added],
  );
  const addedIds = useMemo(
    () => new Set(added.map((item) => item.instrument.instrument_id)),
    [added],
  );
  const changeAdded = (next: InstrumentListing[]) => {
    setAdded(next);
    saveAddedInstruments(storage, next);
  };
  const [instrumentsError, setInstrumentsError] = useState<string | null>(null);
  const [instrumentsReady, setInstrumentsReady] = useState(false);
  const [ledgers, setLedgers] = useState<{
    manual: ManualLedgerResponse | null;
    imported: ImportedLedgerResponse | null;
    broker: BrokerLedgerResponse | null;
    scenarios: ScenarioLedgerResponse | null;
    // Kept raw and worded at render time, in the current language.
    error: { cause: unknown } | null;
  }>({
    manual: null,
    imported: null,
    broker: null,
    scenarios: null,
    error: null,
  });
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
      // The panel lists the newest executions; positions cover the whole ledger.
      api.brokerLedger({ limit: JOURNAL_BROKER_ROWS }),
      api.scenarioLedger(),
    ])
      .then(([manual, imported, broker, scenarios]) =>
        setLedgers({ manual, imported, broker, scenarios, error: null }),
      )
      .catch((error: unknown) =>
        setLedgers({
          manual: null,
          imported: null,
          broker: null,
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
  // Fills of one backtest run drawn on the charts at the user's request.
  const [backtestShown, setBacktestShown] = useState<{
    runId: string;
    fills: BacktestFill[];
  } | null>(null);
  // Python indicators: every saved version is registered so pinned ones still draw.
  const [customVersion, setCustomVersion] = useState(0);
  const [formulaOpen, setFormulaOpen] = useState(false);
  const [pineOpen, setPineOpen] = useState(false);
  // The bars each chart computes indicators on, for Python indicator previews.
  const chartBars = useRef<OhlcvBar[][]>([]);
  const onChartBars = useMemo(
    () =>
      Array.from({ length: CHART_SLOTS }, (_, index) => (items: OhlcvBar[]) => {
        chartBars.current[index] = items;
      }),
    [],
  );
  const [customEditor, setCustomEditor] = useState<{
    initial: CustomIndicator | null;
  } | null>(null);
  useEffect(() => {
    if (!customApi) return;
    let current = true;
    customApi
      .list()
      .then((items) => {
        if (!current) return;
        registerCustom(items);
        setCustomVersion((version) => version + 1);
      })
      .catch(() => undefined); // without SIW_DATA_DIR there are simply none
    return () => {
      current = false;
    };
  }, [customApi]);
  const [agentRequest, setAgentRequest] = useState<ChartReviewRequest | null>(
    null,
  );
  const askAgent = useCallback((request: ChartReviewRequest) => {
    setAgentRequest(request);
    setPanel("review");
  }, []);
  const clearAgentRequest = useCallback(() => setAgentRequest(null), []);
  // Broker executions are read per instrument (the whole ledger can hold tens of
  // thousands): the charts' instruments plus those a backtest result compares.
  const [extraRealIds, setExtraRealIds] = useState<string[]>([]);
  const wantedReal = useMemo(
    () =>
      [
        ...new Set([
          ...layout.charts.map((chart) => chart.instrumentId),
          ...extraRealIds,
        ]),
      ].sort(),
    [layout.charts, extraRealIds],
  );
  const wantedRealKey = wantedReal.join("|");
  const [brokerFills, setBrokerFills] = useState<
    Map<string, BrokerOrderExecution[]>
  >(new Map());
  useEffect(() => {
    let current = true;
    const ids = wantedRealKey ? wantedRealKey.split("|") : [];
    // Re-read after every journal refresh (a sync may have added executions).
    if (ledgers.broker === null) return;
    void Promise.all(
      ids.map((instrumentId) =>
        api
          .brokerLedger({ instrumentId })
          .then((response) => [instrumentId, response.executions] as const)
          .catch(() => [instrumentId, [] as BrokerOrderExecution[]] as const),
      ),
    ).then((pairs) => {
      if (current) setBrokerFills(new Map(pairs));
    });
    return () => {
      current = false;
    };
  }, [api, wantedRealKey, ledgers.broker]);
  const realFills = useMemo(
    () => ({
      manual: ledgers.manual?.executions ?? [],
      imported: ledgers.imported?.executions ?? [],
      broker: [...brokerFills.values()].flat(),
    }),
    [ledgers, brokerFills],
  );
  const chartJournal = useMemo(
    () => ({ ...journal, broker: realFills.broker }),
    [journal, realFills],
  );
  const symbolOf = (instrumentId: string) =>
    instruments.find((item) => item.instrument.instrument_id === instrumentId)
      ?.instrument.symbol ??
    // Not in the loaded list (e.g. a ledger-only ticker): "us:aaoi:xnas:2026" -> AAOI.
    (instrumentId.split(":")[1]?.toUpperCase() || instrumentId);

  const setLayout = (update: (previous: WorkspaceState) => WorkspaceState) =>
    setLibrary((previous) =>
      updateActiveLayout(previous, update(activeWorkspace(previous).layout)),
    );

  // Opened on today: a chart whose exchange is closed today moves to its last session.
  const rolledToSession = useRef(false);
  useEffect(() => {
    if (!openOnToday || rolledToSession.current) return;
    rolledToSession.current = true;
    const zones = [US_ZONE, KR_ZONE];
    void Promise.all(
      zones.map(async (zone) => {
        const today = todayIn(zone);
        try {
          const [last] = await api.sessions(today, 1, zone.calendarId);
          return { zone, today, last: last?.session_date ?? today };
        } catch {
          return { zone, today, last: today };
        }
      }),
    ).then((found) => {
      const moves = found.filter((item) => item.last !== item.today);
      if (moves.length === 0) return;
      setLibrary((previous) => {
        const current = activeWorkspace(previous).layout;
        return updateActiveLayout(previous, {
          ...current,
          charts: current.charts.map((chart) => {
            const move = moves.find(
              (item) =>
                item.zone === zoneOf(chart.instrumentId) &&
                chart.sessionDate === item.today,
            );
            return move ? { ...chart, sessionDate: move.last } : chart;
          }),
        });
      });
    });
  }, [api, openOnToday]);

  // Typing a letter or digit on the chart opens the symbol search with it, as in
  // TradingView; Ctrl/⌘+K opens it empty. Keys typed into fields are left alone.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.isContentEditable ||
          ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName))
      ) {
        return;
      }
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearch("");
        return;
      }
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (/^[a-z0-9]$/i.test(event.key)) {
        event.preventDefault();
        setSearch((current) =>
          current === null ? event.key.toUpperCase() : current,
        );
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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

  const updateChart = (index: number) => (config: ChartConfig) =>
    setLayout((previous) => {
      const charts = [...previous.charts];
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
  const updateActive = (changes: Partial<ChartConfig>) => {
    // A different day or symbol has nothing to do with the replay in progress.
    if (
      (changes.sessionDate &&
        changes.sessionDate !== activeConfig.sessionDate) ||
      (changes.instrumentId &&
        changes.instrumentId !== activeConfig.instrumentId)
    ) {
      setReplay(REPLAY_OFF);
    }
    updateChart(activeChart)({ ...activeConfig, ...changes });
  };
  const replayStep = timeframeSeconds(activeConfig.timeframe);
  const replayLast = bounds[activeChart].last;
  const replayFirst = bounds[activeChart].first;
  const advanceReplay = useCallback(
    () =>
      setReplay((current) => {
        if (current.time === null) return current;
        const next = stepForward(current.time, replayStep, replayLast);
        return next === null
          ? { ...current, playing: false }
          : { ...current, time: next };
      }),
    [replayStep, replayLast],
  );
  useEffect(() => {
    if (!replay.playing) return;
    const timer = window.setInterval(advanceReplay, 1_000 / replay.speed);
    return () => window.clearInterval(timer);
  }, [replay.playing, replay.speed, advanceReplay]);
  const activeSymbol = symbolOf(activeConfig.instrumentId);
  const query = (search ?? "").trim();
  const matches = instruments.filter((item) =>
    item.instrument.symbol.toLowerCase().includes(query.toLowerCase()),
  );
  const localIds = new Set(matches.map((m) => m.instrument.instrument_id));
  const remoteMatches =
    remote && remote.query === query && query
      ? remote.results.filter(
          (item) => !localIds.has(item.listing.instrument.instrument_id),
        )
      : [];
  const searching = query !== "" && (remote === null || remote.query !== query);
  const chooseInstrument = (instrumentId: string) => {
    setSearch(null);
    updateActive({ instrumentId });
  };
  const chooseListing = (
    listing: InstrumentListing,
    lastTradeDate?: string,
  ) => {
    const id = listing.instrument.instrument_id;
    if (!instruments.some((item) => item.instrument.instrument_id === id)) {
      changeAdded([...added, listing]);
    }
    setSearch(null);
    // A delisted stock has no bars after it left its exchange (ADR 0033): open it on its
    // last trade date instead of today.
    updateActive(
      lastTradeDate
        ? { instrumentId: id, sessionDate: lastTradeDate }
        : { instrumentId: id },
    );
  };
  useEffect(() => {
    if (!query) return;
    let current = true;
    // Wait for a pause in typing so each keystroke is not a request.
    const timer = window.setTimeout(() => {
      api
        .searchSymbols(query, 20)
        .then((response) => {
          if (current)
            setRemote({
              query,
              results: response.results,
              stale: response.stale,
              error: null,
            });
        })
        .catch((error: unknown) => {
          if (current) setRemote({ query, results: [], stale: false, error });
        });
    }, 250);
    return () => {
      current = false;
      window.clearTimeout(timer);
    };
  }, [api, query]);
  const layoutMode: LayoutMode = layout.layoutMode ?? (legacySplit ? "2" : "1");
  const setLayoutMode = (mode: LayoutMode) => {
    setLayout((previous) => ({ ...previous, layoutMode: mode }));
    if (mode === "2" && activeChart > 1) setActiveChart(0);
  };
  const visibleCharts =
    layoutMode === "4"
      ? [0, 1, 2, 3]
      : layoutMode === "2"
        ? [0, 1]
        : [activeChart];
  const watchlistVisible = panel === "watchlist" || floating.open;

  // Realtime relay: trades and order book of the charts on screen, plus order events.
  const [live, setLive] = useState<Record<string, LiveSymbol>>({});
  const [liveConnected, setLiveConnected] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const realtimeRef = useRef<RealtimeSocket | null>(null);
  useEffect(() => {
    const socket = api.openRealtime((event) => {
      if (event.event === "trade" || event.event === "orderbook") {
        setLive((previous) => applyLive(previous, event));
      } else if (event.event === "status") {
        setLiveConnected(event.connected);
      } else if (event.event === "order") {
        setNotice(
          t("live.order", {
            symbol: event.symbol,
            side: event.side,
            status: event.status,
            filled: event.filled_quantity ?? "0",
          }),
        );
      } else if (event.event === "ledger_synced") {
        refreshJournal();
        if (event.stored_new > 0) {
          setNotice(t("live.ledger", { n: event.stored_new }));
        }
      }
    });
    realtimeRef.current = socket;
    return () => {
      socket.close();
      realtimeRef.current = null;
    };
    // The socket lives as long as the API; t and refreshJournal only word events.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api]);
  const liveSymbols = [
    ...new Set(
      visibleCharts
        .map((index) => symbolOf(layout.charts[index].instrumentId))
        // US tickers, and Korean KRX codes (six characters, starting with a digit).
        .filter((symbol) =>
          /^([A-Z][A-Z0-9.]{0,9}|[0-9][0-9A-Z]{5})$/.test(symbol),
        ),
    ),
  ].sort();
  const liveKey = replayOn ? "" : liveSymbols.join(",");
  useEffect(() => {
    // Replay shows the past, so nothing live is requested meanwhile.
    realtimeRef.current?.setSymbols(liveKey ? liveKey.split(",") : []);
  }, [liveKey]);
  useEffect(() => {
    if (notice === null) return;
    const timer = window.setTimeout(() => setNotice(null), 8_000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const quoteRows = useSessionQuotes(
    api,
    instruments,
    activeConfig.sessionDate,
    watchlistVisible && visibleCharts.every((index) => chartSettled[index]),
  );
  const setChartTool = (patch: Partial<ChartTools>) =>
    setTools((previous) => {
      const next = [...previous];
      next[activeChart] = { ...next[activeChart], ...patch };
      return next;
    });
  const tool = tools[activeChart];

  return (
    <div className={`terminal-shell${panel ? " with-panel" : ""}`}>
      <header className="top-bar" role="toolbar" aria-label={t("tb.label")}>
        <span className="brand" title="SIW Research">
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
                  if (event.key !== "Enter") return;
                  if (matches[0])
                    chooseInstrument(matches[0].instrument.instrument_id);
                  else if (remoteMatches[0])
                    chooseListing(remoteMatches[0].listing);
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
                {remoteMatches.map((item) => (
                  <li key={item.listing.instrument.instrument_id}>
                    <button
                      type="button"
                      onClick={() =>
                        chooseListing(item.listing, item.last_trade_date)
                      }
                    >
                      <strong>{item.listing.instrument.symbol}</strong>
                      <small>
                        {item.local_name ? `${item.local_name} · ` : ""}
                        {item.name} · {item.listing.instrument.venue}
                        {item.former_symbol
                          ? ` · ${t("chart.formerSymbol", { symbol: item.former_symbol })}`
                          : ""}
                        {item.status === "delisted"
                          ? ` · ${t("chart.delisted", {
                              date: item.last_trade_date ?? "?",
                              moved: item.moved_to
                                ? t("chart.movedTo", { symbol: item.moved_to })
                                : "",
                            })}`
                          : ""}
                      </small>
                    </button>
                  </li>
                ))}
                {searching ? (
                  <li className="symbol-empty">{t("chart.searching")}</li>
                ) : remote?.error ? (
                  <li className="symbol-empty">
                    {remote.error instanceof MarketApiError &&
                    remote.error.code === "symbol_search_unavailable"
                      ? t("chart.searchOffline")
                      : t("chart.searchFailed", {
                          error:
                            remote.error instanceof Error
                              ? remote.error.message
                              : t("common.requestFailed"),
                        })}
                  </li>
                ) : matches.length === 0 && remoteMatches.length === 0 ? (
                  <li className="symbol-empty">{t("chart.noMatch")}</li>
                ) : null}
                {remote?.stale && remote.query === query ? (
                  <li className="symbol-empty">{t("chart.searchStale")}</li>
                ) : null}
              </ul>
            </div>
          ) : null}
        </div>
        <span className="tb-divider" aria-hidden="true" />
        <TimeframePicker
          value={activeConfig.timeframe}
          favorites={favorites}
          onChange={(timeframe) => updateActive({ timeframe })}
          onToggleFavorite={toggleFavorite}
        />
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
          aria-expanded={indicatorsOpen}
          onClick={() => setIndicatorsOpen(true)}
        >
          <IndicatorsIcon />
          {t("tb.indicators")}
        </button>
        <div className="tb-group" role="group" aria-label={t("tb.layout")}>
          {(
            [
              ["1", "tb.singleLayout", SingleLayoutIcon],
              ["2", "tb.splitLayout", LayoutIcon],
              ["4", "tb.gridLayout", GridLayoutIcon],
            ] as const
          ).map(([mode, key, ModeIcon]) => (
            <button
              key={mode}
              type="button"
              className="tb-button"
              aria-label={t(key)}
              title={t(key)}
              aria-pressed={layoutMode === mode}
              onClick={() => setLayoutMode(mode)}
            >
              <ModeIcon />
            </button>
          ))}
        </div>
        <button
          type="button"
          className="tb-button"
          aria-pressed={replayOn}
          title={t("replay.help")}
          onClick={() =>
            setReplay(
              replayOn ? REPLAY_OFF : { ...REPLAY_OFF, phase: "choosing" },
            )
          }
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
          aria-pressed={drawTool === "cursor"}
          aria-label={t("tools.crosshair")}
          title={t("tools.crosshair")}
          onClick={() => setDrawTool("cursor")}
        >
          <CrosshairIcon />
        </button>
        {(
          [
            ["trend", "tools.trendLine", TrendLineIcon],
            ["hline", "tools.horizontalLine", HorizontalLinesIcon],
            ["fib", "tools.fib", FibIcon],
            ["fibext", "tools.fibext", FibExtensionIcon],
            ["text", "tools.text", TextIcon],
            ["measure", "tools.measure", RulerIcon],
            ["zoom", "tools.zoom", ZoomIcon],
          ] as const
        ).map(([id, key, ToolIcon]) => (
          <button
            key={id}
            type="button"
            aria-pressed={drawTool === id}
            aria-label={t(key)}
            title={t(key)}
            onClick={() => setDrawTool(drawTool === id ? "cursor" : id)}
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
          aria-label={t("tools.removeDrawings")}
          title={t("tools.removeDrawings")}
          disabled={
            !(activeConfig.drawings ?? []).some(
              (item) => item.instrumentId === activeConfig.instrumentId,
            )
          }
          onClick={() => {
            // Undoable like other layout replacements.
            const charts = [...layout.charts];
            charts[activeChart] = {
              ...activeConfig,
              drawings: (activeConfig.drawings ?? []).filter(
                (item) => item.instrumentId !== activeConfig.instrumentId,
              ),
            };
            replaceLayout({ ...layout, charts });
          }}
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
        <div className={`chart-grid layout-${layoutMode}`}>
          {visibleCharts.map((index) => (
            <ChartPanel
              key={index}
              active={layoutMode !== "1" && index === activeChart}
              onActivate={() => setActiveChart(index)}
              chartId={`chart-${index + 1}`}
              title={t("ws.chartTitle", { n: index + 1 })}
              api={api}
              config={layout.charts[index]}
              instruments={instruments}
              instrumentsReady={instrumentsReady}
              sync={sync}
              journal={chartJournal}
              backtestFills={backtestShown?.fills}
              onAskAgent={agentApi ? askAgent : undefined}
              customApi={customApi}
              customVersion={customVersion}
              onBars={onChartBars[index]}
              onJournalChange={refreshJournal}
              magnet={tools[index].magnet}
              logScale={tools[index].logScale}
              fitRequest={tools[index].fitRequest}
              onSettledChange={onChartSettled[index]}
              replayTime={replay.phase === "running" ? replay.time : null}
              replayChoosing={
                replay.phase === "choosing" && index === activeChart
              }
              onReplayPick={(time) => {
                setActiveChart(index);
                setReplay({
                  ...replay,
                  phase: "running",
                  time,
                  playing: false,
                });
              }}
              onReplayBounds={onBounds[index]}
              onIndicatorsChange={(indicators) =>
                updateChart(index)({
                  ...layout.charts[index],
                  indicators,
                })
              }
              tool={index === activeChart ? drawTool : "cursor"}
              onDrawingsChange={(drawings) =>
                updateChart(index)({
                  ...layout.charts[index],
                  drawings,
                })
              }
              onToolDone={() => setDrawTool("cursor")}
              newId={newId}
            />
          ))}
        </div>
        {notice ? (
          <div className="live-notice" role="status">
            {notice}
          </div>
        ) : null}
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
                aria-pressed={
                  value === activeConfig.sessionScope ||
                  (value === "extended" &&
                    activeConfig.sessionScope === "overnight")
                }
                // Daily and weekly bars always cover the regular session.
                disabled={isDailyTimeframe(activeConfig.timeframe)}
                title={
                  isDailyTimeframe(activeConfig.timeframe)
                    ? t("tf.dailyHours")
                    : undefined
                }
                onClick={() => updateActive({ sessionScope: value })}
              >
                {t(
                  value === "regular"
                    ? "chart.regular"
                    : activeConfig.instrumentId.startsWith("us:")
                      ? "chart.extendedUs"
                      : "chart.extended",
                )}
              </button>
            ))}
          </div>
          <span className="tb-spacer" />
          <ExchangeClock zone={zoneOf(activeConfig.instrumentId)} />
        </footer>
        {backtestShown ? (
          <div className="bt-chip" role="status" title={t("bt.subtitle")}>
            {t("bt.chip", { n: backtestShown.fills.length })}
            <button
              type="button"
              className="tb-button"
              aria-label={t("bt.hideOnChart")}
              onClick={() => setBacktestShown(null)}
            >
              {t("bt.chipHide")}
            </button>
          </div>
        ) : null}
        {replayOn ? (
          <div
            className="replay-bar"
            role="toolbar"
            aria-label={t("replay.controls")}
          >
            <strong className="replay-badge">{t("tb.replay")}</strong>
            {replay.phase === "choosing" ? (
              <span className="replay-hint">{t("replay.choose")}</span>
            ) : (
              <>
                <span className="replay-time" aria-live="off">
                  {replay.time === null
                    ? ""
                    : replayTimeText(
                        replay.time,
                        zoneOf(activeConfig.instrumentId),
                      )}
                </span>
                <button
                  type="button"
                  className="tb-button"
                  aria-label={t("replay.back")}
                  disabled={
                    replay.time === null ||
                    (replayFirst !== null && replay.time <= replayFirst)
                  }
                  onClick={() =>
                    setReplay((current) =>
                      current.time === null
                        ? current
                        : {
                            ...current,
                            playing: false,
                            time: stepBack(
                              current.time,
                              replayStep,
                              replayFirst,
                            ),
                          },
                    )
                  }
                >
                  ⏮
                </button>
                <button
                  type="button"
                  className="tb-button"
                  aria-label={t(
                    replay.playing ? "replay.pause" : "replay.play",
                  )}
                  onClick={() =>
                    setReplay((current) => ({
                      ...current,
                      playing: !current.playing,
                    }))
                  }
                >
                  {replay.playing ? "⏸" : "▶"}
                </button>
                <button
                  type="button"
                  className="tb-button"
                  aria-label={t("replay.forward")}
                  disabled={
                    replay.time === null ||
                    (replayLast !== null && replay.time >= replayLast)
                  }
                  onClick={advanceReplay}
                >
                  ⏭
                </button>
                <div
                  className="tb-group"
                  role="group"
                  aria-label={t("replay.speed")}
                >
                  {REPLAY_SPEEDS.map((speed) => (
                    <button
                      key={speed}
                      type="button"
                      className="tb-button"
                      aria-pressed={replay.speed === speed}
                      onClick={() =>
                        setReplay((current) => ({ ...current, speed }))
                      }
                    >
                      {speed}x
                    </button>
                  ))}
                </div>
                <span className="replay-hint">
                  {t("replay.step", { timeframe: activeConfig.timeframe })}
                </span>
              </>
            )}
            <span className="tb-spacer" />
            <button
              type="button"
              className="tb-button"
              onClick={() => setReplay(REPLAY_OFF)}
            >
              {t("replay.exit")}
            </button>
          </div>
        ) : null}
      </main>

      {panel === "backtest" && backtestApi ? (
        <BacktestScreen
          api={backtestApi}
          instruments={instruments}
          activeInstrumentId={activeConfig.instrumentId}
          real={realFills}
          onNeedReal={setExtraRealIds}
          agentApi={agentApi}
          shownRunId={backtestShown?.runId ?? null}
          onShowFills={setBacktestShown}
          onClose={() => setPanel(null)}
        />
      ) : null}

      {panel === "tradeReview" && reviewApi ? (
        <TradeReviewScreen
          api={reviewApi}
          marketApi={api}
          instruments={instruments}
          activeInstrumentId={activeConfig.instrumentId}
          onClose={() => setPanel(null)}
        />
      ) : null}

      {panel === "keys" && keysPanel ? (
        <div
          className="modal-backdrop"
          onPointerDown={(event) => {
            if (event.target === event.currentTarget) setPanel(null);
          }}
        >
          <section
            className="modal keys-modal"
            role="dialog"
            aria-modal="true"
            aria-label={t("panel.keys")}
            onKeyDown={(event) => {
              if (event.key === "Escape") setPanel(null);
            }}
          >
            <button
              type="button"
              className="tb-button keys-modal-close"
              aria-label={t("keys.close")}
              onClick={() => setPanel(null)}
            >
              ✕
            </button>
            {keysPanel}
          </section>
        </div>
      ) : null}

      {panel &&
      panel !== "backtest" &&
      panel !== "tradeReview" &&
      panel !== "keys" ? (
        <aside
          className={`side-panel side-panel-${panel}`}
          aria-label={t(`panel.${panel}`)}
        >
          {panel === "watchlist" && replayOn ? (
            <p className="workspace-note" role="status">
              {t("replay.watchlistHidden")}
            </p>
          ) : null}
          {panel === "watchlist" && !replayOn ? (
            <Watchlist
              instruments={instruments}
              rows={quoteRows}
              sessionDate={activeConfig.sessionDate}
              activeInstrumentId={activeConfig.instrumentId}
              onPick={(instrumentId) => updateActive({ instrumentId })}
              removable={addedIds}
              onRemove={(instrumentId) =>
                changeAdded(
                  added.filter(
                    (item) => item.instrument.instrument_id !== instrumentId,
                  ),
                )
              }
              actions={
                <button
                  type="button"
                  className="tb-button"
                  aria-label={t("watch.popOut")}
                  title={t("watch.popOut")}
                  onClick={popOutWatchlist}
                >
                  <PopOutIcon />
                </button>
              }
            />
          ) : null}
          {panel === "quote" ? (
            <QuotePanel
              api={api}
              instrumentId={activeConfig.instrumentId}
              symbol={activeSymbol}
              replayOn={replayOn}
              live={live[activeSymbol] ?? null}
              liveConnected={liveConnected}
            />
          ) : null}
          {panel === "info" ? (
            <StockInfoPanel
              api={api}
              instrumentId={activeConfig.instrumentId}
              symbol={activeSymbol}
            />
          ) : null}
          {panel === "research" ? <ResearchPanel api={api} /> : null}
          {panel === "account" ? (
            <AccountPanel api={api} onSynced={refreshJournal} />
          ) : null}
          {panel === "journal" ? (
            <>
              <JournalPanel
                manual={ledgers.manual}
                imported={ledgers.imported}
                broker={ledgers.broker}
                scenarios={ledgers.scenarios}
                error={journalError}
                symbolOf={symbolOf}
              />
              {journalError ? null : (
                <>
                  <ReconciliationView
                    api={api}
                    revision={ledgers}
                    symbolOf={symbolOf}
                  />
                  <ImportPanel api={api} onImported={refreshJournal} />
                </>
              )}
            </>
          ) : null}
          {panel === "review" && agentApi ? (
            <AgentPanel
              api={agentApi}
              instrumentId={activeConfig.instrumentId}
              timeframe={activeConfig.timeframe}
              cutoffUtc={() =>
                new Date(
                  replay.phase === "running" && replay.time !== null
                    ? replay.time * 1000
                    : Date.now(),
                )
                  .toISOString()
                  .replace(/\.\d{3}Z$/, "Z")
              }
              real={realFills}
              request={agentRequest}
              onRequestHandled={clearAgentRequest}
              symbolOf={symbolOf}
            />
          ) : null}
        </aside>
      ) : null}

      <nav className="right-rail" aria-label={t("panel.rail")}>
        {(
          [
            ["watchlist", WatchlistIcon],
            ["quote", QuoteIcon],
            ["info", InfoIcon],
            ["research", ResearchIcon],
            ["account", AccountIcon],
            ["journal", JournalIcon],
            ...(backtestApi ? ([["backtest", BacktestIcon]] as const) : []),
            ...(reviewApi ? ([["tradeReview", TradeReviewIcon]] as const) : []),
            ...(agentApi ? ([["review", AgentIcon]] as const) : []),
            ...(keysPanel ? ([["keys", KeyIcon]] as const) : []),
          ] as const
        ).map(([id, RailIcon]) => (
          <button
            key={id}
            type="button"
            aria-label={t(`panel.${id}`)}
            title={t(`panel.${id}`)}
            aria-pressed={panel === id || (id === "watchlist" && floating.open)}
            onClick={() => {
              if (id === "watchlist" && floating.open) {
                // The watchlist is floating: the rail button closes the window.
                setFloating({ ...floating, open: false });
                return;
              }
              setPanel(panel === id ? null : id);
            }}
          >
            <RailIcon />
          </button>
        ))}
      </nav>

      {indicatorsOpen ? (
        <IndicatorDialog
          target={`${activeSymbol} · ${activeConfig.timeframe}`}
          onAdd={(definition) =>
            updateActive({
              indicators: [
                ...(activeConfig.indicators ?? []),
                {
                  key: newId(),
                  type: definition.id,
                  params: defaultParams(definition),
                },
              ],
            })
          }
          onClose={() => setIndicatorsOpen(false)}
          custom={customApi ? latestCustom() : undefined}
          onEditCustom={(initial) => setCustomEditor({ initial })}
          onNewFormula={() => setFormulaOpen(true)}
          onNewPine={() => setPineOpen(true)}
        />
      ) : null}

      {formulaOpen ? (
        <FormulaDialog
          target={`${activeSymbol} · ${activeConfig.timeframe}`}
          onClose={() => setFormulaOpen(false)}
          onAdd={(value) => {
            setFormulaOpen(false);
            setIndicatorsOpen(false);
            updateActive({
              indicators: [
                ...(activeConfig.indicators ?? []),
                { key: newId(), type: "formula", params: {}, ...value },
              ],
            });
          }}
        />
      ) : null}

      {pineOpen ? (
        <PineDialog
          target={`${activeSymbol} · ${activeConfig.timeframe}`}
          onClose={() => setPineOpen(false)}
          onAdd={(source) => {
            setPineOpen(false);
            setIndicatorsOpen(false);
            const widths = Object.entries(pineWidths(source));
            updateActive({
              indicators: [
                ...(activeConfig.indicators ?? []),
                {
                  key: newId(),
                  type: "pine",
                  params: defaultParams(pineDefinition(source)),
                  pine: source,
                  ...(widths.length > 0
                    ? {
                        style: {
                          outputs: Object.fromEntries(
                            widths.map(([key, width]) => [key, { width }]),
                          ),
                        },
                      }
                    : {}),
                },
              ],
            });
          }}
        />
      ) : null}

      {customEditor && customApi ? (
        <CustomIndicatorEditor
          api={customApi}
          previewSource={() => ({
            label: `${activeSymbol} · ${activeConfig.timeframe}`,
            bars: chartBars.current[activeChart] ?? [],
            timeZone: zoneOf(activeConfig.instrumentId).timeZone,
          })}
          initial={customEditor.initial}
          onClose={() => setCustomEditor(null)}
          onSaved={(saved, addToChart) => {
            registerCustom([saved]);
            setCustomVersion((version) => version + 1);
            setCustomEditor(null);
            if (addToChart) {
              setIndicatorsOpen(false);
              updateActive({
                indicators: [
                  ...(activeConfig.indicators ?? []),
                  {
                    key: newId(),
                    type: saved.type,
                    params: Object.fromEntries(
                      saved.inputs.map((input) => [input.key, input.default]),
                    ),
                  },
                ],
              });
            }
          }}
        />
      ) : null}

      {floating.open ? (
        <FloatingWindow
          title={t("watch.title")}
          geometry={floating.geometry}
          onGeometryChange={(geometry) => setFloating({ open: true, geometry })}
          actions={
            <>
              <button
                type="button"
                className="tb-button"
                aria-label={t("watch.dock")}
                title={t("watch.dock")}
                onClick={dockWatchlist}
              >
                <DockIcon />
              </button>
              <button
                type="button"
                className="tb-button"
                aria-label={t("watch.close")}
                title={t("watch.close")}
                onClick={() => setFloating({ ...floating, open: false })}
              >
                <CloseIcon />
              </button>
            </>
          }
        >
          {replayOn ? (
            <p className="workspace-note" role="status">
              {t("replay.watchlistHidden")}
            </p>
          ) : (
            <Watchlist
              instruments={instruments}
              rows={quoteRows}
              sessionDate={activeConfig.sessionDate}
              activeInstrumentId={activeConfig.instrumentId}
              onPick={(instrumentId) => updateActive({ instrumentId })}
              removable={addedIds}
              onRemove={(instrumentId) =>
                changeAdded(
                  added.filter(
                    (item) => item.instrument.instrument_id !== instrumentId,
                  ),
                )
              }
              hideTitle
            />
          )}
        </FloatingWindow>
      ) : null}
    </div>
  );
}
