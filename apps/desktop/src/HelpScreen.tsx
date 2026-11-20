import { useEffect, useMemo, useRef } from "react";

import featuresGuide from "../../../docs/FEATURES.md?raw";
import {
  helpHeadings,
  parseHelp,
  plainText,
  renderHelp,
} from "./help-markdown";
import { useI18n } from "./i18n";

interface HelpScreenProps {
  onClose: () => void;
  /** The guide's text; docs/FEATURES.md, bundled at build time, by default. */
  source?: string;
}

/** Help → the implemented-features guide (docs/FEATURES.md), read inside the app. */
export function HelpScreen({
  onClose,
  source = featuresGuide,
}: HelpScreenProps) {
  const { t, language } = useI18n();
  const blocks = useMemo(() => parseHelp(source), [source]);
  const contents = useMemo(
    () => helpHeadings(blocks).filter((heading) => heading.level === 2),
    [blocks],
  );
  const body = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const jump = (id: string) => {
    body.current
      ?.querySelector(`#${id}`)
      ?.scrollIntoView({ block: "start", behavior: "smooth" });
  };

  return (
    <div
      className="modal-backdrop bt-backdrop"
      onPointerDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <section
        className="modal bt-screen help-screen"
        role="dialog"
        aria-label={t("help.title")}
      >
        <header className="modal-head">
          <h2>{t("help.title")}</h2>
          {language !== "ko" ? (
            <span className="workspace-note">{t("help.koreanOnly")}</span>
          ) : null}
          <span className="tb-spacer" />
          <button type="button" className="tb-button" onClick={onClose}>
            {t("help.close")}
          </button>
        </header>
        <div className="help-layout">
          <nav className="help-toc" aria-label={t("help.contents")}>
            <strong>{t("help.contents")}</strong>
            <ol>
              {contents.map((heading) => (
                <li key={heading.id}>
                  <button
                    type="button"
                    className="help-toc-link"
                    onClick={() => jump(heading.id)}
                  >
                    {plainText(heading.text)}
                  </button>
                </li>
              ))}
            </ol>
          </nav>
          <div className="help-body" ref={body}>
            {renderHelp(blocks)}
          </div>
        </div>
      </section>
    </div>
  );
}
