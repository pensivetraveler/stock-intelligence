/**
 * Workspace frame parts (디자인-siw-1 r4): the collapsible left sidebar grouped by task,
 * tabs inside a side panel, and one dialog frame (focus in, Esc and ✕ to close, focus back).
 */
import {
  useEffect,
  useRef,
  type ComponentType,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import { useI18n, type MessageKey } from "./i18n";
import {
  AccountIcon,
  AgentIcon,
  BacktestIcon,
  QuoteIcon,
  ScreenerIcon,
  SettingsIcon,
  SidebarIcon,
  TradeReviewIcon,
  WatchlistIcon,
} from "./icons";

export type NavItemId =
  | "watchlist"
  | "symbol"
  | "discover"
  | "ledger"
  | "notes"
  | "tradeReview"
  | "lab"
  | "settings";

interface NavItem {
  id: NavItemId;
  label: MessageKey;
  Icon: ComponentType;
  /** Shortcut shown in the tooltip (⌘1–⌘4 open the first item of a group). */
  keys?: string;
  /** Work screens open beside the chart. */
  work?: boolean;
}

const GROUPS: { label: MessageKey; items: NavItem[] }[] = [
  {
    label: "nav.group.symbol",
    items: [
      {
        id: "watchlist",
        label: "panel.watchlist",
        Icon: WatchlistIcon,
        keys: "⌘1",
      },
      { id: "symbol", label: "panel.symbol", Icon: QuoteIcon },
    ],
  },
  {
    label: "nav.group.discover",
    items: [
      {
        id: "discover",
        label: "panel.discover",
        Icon: ScreenerIcon,
        keys: "⌘2",
      },
    ],
  },
  {
    label: "nav.group.trade",
    items: [
      { id: "ledger", label: "panel.ledger", Icon: AccountIcon, keys: "⌘3" },
      { id: "notes", label: "panel.notes", Icon: AgentIcon },
    ],
  },
  {
    label: "nav.group.work",
    items: [
      {
        id: "tradeReview",
        label: "panel.tradeReview",
        Icon: TradeReviewIcon,
        keys: "⌘4",
        work: true,
      },
      { id: "lab", label: "panel.lab", Icon: BacktestIcon, work: true },
    ],
  },
];

const SETTINGS: NavItem = {
  id: "settings",
  label: "panel.settings",
  Icon: SettingsIcon,
  keys: "⌘,",
};

export function SideNav({
  panel,
  collapsed,
  onToggle,
  onOpen,
  watchlistFloating,
  has,
}: {
  panel: NavItemId | null;
  collapsed: boolean;
  onToggle: () => void;
  onOpen: (id: NavItemId) => void;
  watchlistFloating: boolean;
  /** Items that need an API this build may not have; missing ones are not shown. */
  has: Partial<Record<NavItemId, boolean>>;
}) {
  const { t } = useI18n();
  const shown = (item: NavItem) => has[item.id] !== false;
  const button = (item: NavItem) => {
    const { Icon } = item;
    const label = t(item.label);
    const tip = [label, item.keys, item.work ? t("nav.workHint") : null]
      .filter(Boolean)
      .join(" · ");
    return (
      <button
        key={item.id}
        type="button"
        className="ws-nav-btn"
        aria-label={label}
        title={tip}
        aria-pressed={
          panel === item.id || (item.id === "watchlist" && watchlistFloating)
        }
        onClick={() => onOpen(item.id)}
      >
        <Icon />
        <span className="ws-nav-label">{label}</span>
        {item.work ? (
          <span className="ws-nav-work" aria-hidden="true">
            ↗
          </span>
        ) : null}
      </button>
    );
  };
  return (
    <nav
      className={`ws-nav${collapsed ? " collapsed" : ""}`}
      aria-label={t("panel.rail")}
    >
      <button
        type="button"
        className="ws-nav-toggle"
        aria-label={collapsed ? t("nav.expand") : t("nav.collapse")}
        title={collapsed ? t("nav.expand") : t("nav.collapse")}
        aria-expanded={!collapsed}
        onClick={onToggle}
      >
        <SidebarIcon />
      </button>
      {GROUPS.map((group) => {
        const items = group.items.filter(shown);
        if (items.length === 0) return null;
        return (
          <div
            key={group.label}
            className="ws-nav-group"
            role="group"
            aria-label={t(group.label)}
          >
            <span className="ws-nav-head" aria-hidden="true">
              {t(group.label)}
            </span>
            {items.map(button)}
          </div>
        );
      })}
      <span className="ws-nav-spacer" />
      {shown(SETTINGS) ? button(SETTINGS) : null}
    </nav>
  );
}

/** Tabs inside one side panel (같은 자리 전환). Arrow keys, Home and End move between tabs. */
export function PanelTabs<T extends string>({
  label,
  value,
  onChange,
  tabs,
}: {
  label: string;
  value: T;
  onChange: (value: T) => void;
  tabs: readonly (readonly [T, string])[];
}) {
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = tabs.findIndex(([id]) => id === value);
    const next =
      event.key === "ArrowRight"
        ? index + 1
        : event.key === "ArrowLeft"
          ? index - 1
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? tabs.length - 1
              : null;
    if (next === null) return;
    event.preventDefault();
    const [id] = tabs[(next + tabs.length) % tabs.length];
    onChange(id);
    const buttons =
      event.currentTarget.querySelectorAll<HTMLButtonElement>("[role=tab]");
    buttons[(next + tabs.length) % tabs.length]?.focus();
  };
  return (
    <div
      className="panel-tabs"
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
    >
      {tabs.map(([id, text]) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={id === value}
          tabIndex={id === value ? 0 : -1}
          onClick={() => onChange(id)}
        >
          {text}
        </button>
      ))}
    </div>
  );
}

/**
 * One dialog frame: modal, first field focused on open, Tab stays inside, Esc / ✕ /
 * outside click close it, and focus goes back to what opened it.
 */
export function Dialog({
  label,
  className,
  onClose,
  closeLabel,
  children,
}: {
  label: string;
  className?: string;
  onClose: () => void;
  closeLabel: string;
  children: ReactNode;
}) {
  const box = useRef<HTMLElement>(null);
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const first = box.current?.querySelector<HTMLElement>(
      "input, select, textarea, button:not(.dialog-close)",
    );
    first?.focus();
    return () => opener?.focus?.();
  }, []);
  const onKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
      return;
    }
    if (event.key !== "Tab" || !box.current) return;
    const focusable = Array.from(
      box.current.querySelectorAll<HTMLElement>(
        "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex='0']",
      ),
    );
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };
  return (
    <div
      className="modal-backdrop"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        ref={box}
        className={`modal${className ? ` ${className}` : ""}`}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        onKeyDown={onKeyDown}
      >
        <header className="modal-head">
          <h2>{label}</h2>
          <button
            type="button"
            className="tb-button dialog-close"
            aria-label={closeLabel}
            onClick={onClose}
          >
            ✕
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
