import { useEffect, useMemo, useRef } from "react";

import featuresGuide from "../../../docs/FEATURES.md?raw";
import {
  helpHeadings,
  parseHelp,
  plainText,
  renderHelp,
} from "./help-markdown";
import { useI18n } from "./i18n";
import { ModalFrame } from "./Shell";

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

  // Esc also closes the guide when focus is outside it (the frame handles Esc inside).
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
    <ModalFrame
      label={t("help.title")}
      className="bt-screen help-screen"
      backdropClassName="bt-backdrop"
      onClose={onClose}
    >
      <header className="modal-head">
        <h2>{t("help.title")}</h2>
        {language !== "ko" ? (
          <span className="workspace-note">{t("help.koreanOnly")}</span>
        ) : null}
        <span className="tb-spacer" />
        <button
          type="button"
          className="tb-button dialog-close"
          onClick={onClose}
        >
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
    </ModalFrame>
  );
}
