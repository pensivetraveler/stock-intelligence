/**
 * Line icons drawn for this app (16x16 grid, stroke only). They follow the dense style of
 * desktop trading terminals without copying any vendor's artwork.
 */
import type { ReactNode } from "react";

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg className="icon" viewBox="0 0 16 16" aria-hidden="true">
      {children}
    </svg>
  );
}

export const CrosshairIcon = () => (
  <Icon>
    <path d="M8 1v5M8 10v5M1 8h5M10 8h5" />
  </Icon>
);
export const TrendLineIcon = () => (
  <Icon>
    <path d="M2.5 13.5l11-11" />
    <circle cx="2.5" cy="13.5" r="1.3" />
    <circle cx="13.5" cy="2.5" r="1.3" />
  </Icon>
);
export const HorizontalLinesIcon = () => (
  <Icon>
    <path d="M1.5 4h13M1.5 8h13M1.5 12h13" />
  </Icon>
);
export const FibIcon = () => (
  <Icon>
    <path d="M1.5 3h13M1.5 6.5h13M1.5 10h13M1.5 13.5h13M3 13.5L13 3" />
  </Icon>
);
export const TextIcon = () => (
  <Icon>
    <path d="M3 3h10M8 3v10.5M6 13.5h4" />
  </Icon>
);
export const RulerIcon = () => (
  <Icon>
    <path d="M1.5 11l9.5-9.5 3.5 3.5L5 14.5z M4 8.5l1.5 1.5M6.5 6l1.5 1.5M9 3.5l1.5 1.5" />
  </Icon>
);
export const ZoomIcon = () => (
  <Icon>
    <circle cx="7" cy="7" r="4.5" />
    <path d="M10.3 10.3L14.5 14.5M5 7h4M7 5v4" />
  </Icon>
);
export const MagnetIcon = () => (
  <Icon>
    <path d="M3.5 2h3v6a1.5 1.5 0 0 0 3 0V2h3v6a4.5 4.5 0 0 1-9 0z M3.5 5h3M9.5 5h3" />
  </Icon>
);
export const LogScaleIcon = () => (
  <Icon>
    <path d="M2 14C5 14 7 11 8 8s3-6 6-6M2 14V2M2 14h12" />
  </Icon>
);
export const FitIcon = () => (
  <Icon>
    <path d="M2 6V2h4M10 2h4v4M14 10v4h-4M6 14H2v-4" />
  </Icon>
);
export const TrashIcon = () => (
  <Icon>
    <path d="M2.5 4h11M6 4V2.5h4V4M4 4l.8 10h6.4L12 4" />
  </Icon>
);
export const CandlesIcon = () => (
  <Icon>
    <path d="M4.5 1.5v13M11.5 1.5v13" />
    <rect x="3" y="4" width="3" height="6" />
    <rect x="10" y="6" width="3" height="5" />
  </Icon>
);
export const IndicatorsIcon = () => (
  <Icon>
    <path d="M1.5 12.5l4-5 3 3 6-7.5M1.5 14.5h13" />
  </Icon>
);
export const LayoutIcon = () => (
  <Icon>
    <rect x="1.5" y="2.5" width="13" height="11" rx="1" />
    <path d="M8 2.5v11" />
  </Icon>
);
export const SingleLayoutIcon = () => (
  <Icon>
    <rect x="1.5" y="2.5" width="13" height="11" rx="1" />
  </Icon>
);
export const GridLayoutIcon = () => (
  <Icon>
    <rect x="1.5" y="2.5" width="13" height="11" rx="1" />
    <path d="M8 2.5v11M1.5 8h13" />
  </Icon>
);
export const ReplayIcon = () => (
  <Icon>
    <path d="M7.5 3.5L2.5 8l5 4.5zM13.5 3.5L8.5 8l5 4.5z" />
  </Icon>
);
export const UndoIcon = () => (
  <Icon>
    <path d="M5 3.5L2 6.5l3 3M2 6.5h7.5a4 4 0 0 1 0 8H7" />
  </Icon>
);
export const SearchIcon = () => (
  <Icon>
    <circle cx="7" cy="7" r="4.5" />
    <path d="M10.3 10.3L14.5 14.5" />
  </Icon>
);
export const WatchlistIcon = () => (
  <Icon>
    <path d="M2 3h12M2 6.5h12M2 10h7M2 13.5h7" />
  </Icon>
);
export const JournalIcon = () => (
  <Icon>
    <path d="M3.5 1.5h8l2 2v11h-10zM6 6h5M6 9h5M6 12h3" />
  </Icon>
);
export const BacktestIcon = () => (
  <Icon>
    <path d="M1.5 14.5h13M2.5 12l3-4 3 2 5-6.5M10.5 3.5h3v3" />
  </Icon>
);
export const AgentIcon = () => (
  <Icon>
    <path d="M2.5 3.5h11v7h-6l-3 3v-3h-2z" />
    <path d="M5.5 7h.01M8 7h.01M10.5 7h.01" />
  </Icon>
);
export const KeyIcon = () => (
  <Icon>
    <circle cx="5" cy="11" r="3" />
    <path d="M7.2 8.8L14 2M11 5l2 2M12.5 3.5l1.5 1.5" />
  </Icon>
);
export const AccountIcon = () => (
  <Icon>
    <rect x="2" y="4" width="12" height="9" rx="1.5" />
    <path d="M2 7h12M5 10.5h2.5" />
  </Icon>
);
export const ResearchIcon = () => (
  <Icon>
    <path d="M2 14h12M4 11V7M7.5 11V4M11 11V8.5" />
  </Icon>
);
export const InfoIcon = () => (
  <Icon>
    <circle cx="8" cy="8" r="6" />
    <path d="M8 7v4.5M8 4.6v.2" />
  </Icon>
);
export const QuoteIcon = () => (
  <Icon>
    <path d="M2 3h5M2 6h5M2 9h5M2 12h5M9 4.5h5M9 7.5h5M9 10.5h5" />
  </Icon>
);
export const DataIcon = () => (
  <Icon>
    <ellipse cx="8" cy="3.5" rx="5.5" ry="2" />
    <path d="M2.5 3.5v9c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2v-9M2.5 8c0 1.1 2.5 2 5.5 2s5.5-.9 5.5-2" />
  </Icon>
);
export const PopOutIcon = () => (
  <Icon>
    <path d="M9 2h5v5M14 2L7.5 8.5M12 9.5V14H2V4h4.5" />
  </Icon>
);
export const DockIcon = () => (
  <Icon>
    <rect x="1.5" y="2.5" width="13" height="11" rx="1" />
    <path d="M10 2.5v11M5 6l2 2-2 2" />
  </Icon>
);
export const CloseIcon = () => (
  <Icon>
    <path d="M3.5 3.5l9 9M12.5 3.5l-9 9" />
  </Icon>
);
export const ChevronDownIcon = () => (
  <Icon>
    <path d="M4 6l4 4 4-4" />
  </Icon>
);

/** The app's own mark: two candles in a rounded square. */
export const AppMark = () => (
  <svg className="app-mark" viewBox="0 0 20 20" aria-hidden="true">
    <rect x="1" y="1" width="18" height="18" rx="5" />
    <path d="M7 4.5v11M13 4.5v11" />
    <rect x="5.5" y="7" width="3" height="5" />
    <rect x="11.5" y="6" width="3" height="4" />
  </svg>
);
