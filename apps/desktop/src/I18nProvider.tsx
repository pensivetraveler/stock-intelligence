import { useEffect, useMemo, useState, type ReactNode } from "react";

import {
  detectLanguage,
  I18nContext,
  LANGUAGE_STORAGE_KEY,
  translator,
  type Language,
} from "./i18n";

function browserStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function I18nProvider({
  children,
  storage = browserStorage(),
}: {
  children: ReactNode;
  storage?: Storage | null;
}) {
  const [language, setLanguage] = useState<Language>(() =>
    detectLanguage(storage),
  );
  useEffect(() => {
    document.documentElement.lang = language;
    try {
      storage?.setItem(LANGUAGE_STORAGE_KEY, language);
    } catch {
      // Not saved: the choice still applies until the page reloads.
    }
  }, [language, storage]);
  const value = useMemo(
    () => ({ language, setLanguage, t: translator(language) }),
    [language],
  );
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}
