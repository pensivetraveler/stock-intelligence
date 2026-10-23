/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL?: string;
  /** Which app this build is (ADR 0037 §7); set from the Vite mode. */
  readonly VITE_SIW_APP: "agent" | "research";
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
