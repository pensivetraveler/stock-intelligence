/**
 * Parts shared by the side panels and work screens (디자인-siw-4 r3): the panel bar that
 * replaces each panel's large heading, a notice box for "needs a key" / failures / gaps,
 * and the numbered steps used where facts are sent to Claude.
 */
import type { ReactNode } from "react";

import { useI18n } from "./i18n";

/** Panel heading row: the title (small) on the left and the panel's own actions on the right. */
export function PanelBar({
  title,
  children,
  as: Heading = "h2",
}: {
  title: ReactNode;
  children?: ReactNode;
  as?: "h2" | "h3";
}) {
  return (
    <div className="panel-bar">
      <Heading>{title}</Heading>
      {children ? <div className="panel-bar-actions">{children}</div> : null}
    </div>
  );
}

/**
 * A boxed notice. "need" is a setting the user still has to make (not an error) and
 * can carry a button to the Connections & keys settings; "fail" is a real failure;
 * "caution" warns about the data (gaps, partial results).
 */
export function Notice({
  tone,
  title,
  children,
  onOpenKeys,
  action,
  role,
}: {
  tone: "need" | "fail" | "caution";
  title?: ReactNode;
  children?: ReactNode;
  onOpenKeys?: () => void;
  action?: ReactNode;
  role?: "status" | "alert";
}) {
  const { t } = useI18n();
  return (
    <div
      className={`notice-box notice-${tone}`}
      role={role ?? (tone === "fail" ? "alert" : "status")}
    >
      {title ? <strong>{title}</strong> : null}
      {children ? <p>{children}</p> : null}
      {onOpenKeys || action ? (
        <div className="notice-actions">
          {onOpenKeys ? (
            <button type="button" className="secondary" onClick={onOpenKeys}>
              {t("parts.openKeys")}
            </button>
          ) : null}
          {action}
        </div>
      ) : null}
    </div>
  );
}

/** One numbered step (1 facts → 2 what will be sent → 3 commentary). */
export function Step({
  n,
  title,
  locked = false,
  children,
}: {
  n: number;
  title: ReactNode;
  locked?: boolean;
  children?: ReactNode;
}) {
  return (
    <section className={`step${locked ? " step-locked" : ""}`}>
      <h3 className="step-title">
        <span className="step-n" aria-hidden="true">
          {n}
        </span>
        {title}
      </h3>
      {children}
    </section>
  );
}

/**
 * One section of the stacked side column (디자인-siw-3 r3): its heading toggles it. A folded
 * section's body is not rendered at all, so whatever it would request is not requested.
 */
export function FoldSection({
  id,
  title,
  open,
  onToggle,
  actions,
  children,
}: {
  id: string;
  title: ReactNode;
  open: boolean;
  onToggle: () => void;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  const body = `fold-${id}`;
  return (
    <section className="fold-section" aria-labelledby={`${body}-head`}>
      <div className="panel-bar fold-bar">
        <h2>
          <button
            id={`${body}-head`}
            type="button"
            aria-expanded={open}
            aria-controls={open ? body : undefined}
            onClick={onToggle}
          >
            {title}
          </button>
        </h2>
        {open && actions ? (
          <div className="panel-bar-actions">{actions}</div>
        ) : null}
      </div>
      {open ? (
        <div id={body} className="fold-body">
          {children}
        </div>
      ) : null}
    </section>
  );
}
