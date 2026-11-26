/**
 * Why the SIW Agent shell could not start the local API (Tauri command
 * `api_start_problem`). Outside Tauri (a browser, tests) there is no shell to ask.
 */
export interface ApiStartProblem {
  kind:
    "port_in_use" | "no_repo" | "no_uv" | "spawn_failed" | "exited" | "timeout";
  port: number;
  /** The program on the port, the exit status or the OS error; may be empty. */
  detail: string;
}

export type ApiStartCheck = () => Promise<ApiStartProblem | null>;

type Invoke = (command: string) => Promise<unknown>;

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function createApiStartCheck(invoke?: Invoke): ApiStartCheck | null {
  if (invoke === undefined && !inTauri()) return null;
  const run =
    invoke ??
    (async (name: string) => {
      const core = await import("@tauri-apps/api/core");
      return core.invoke(name);
    });
  return async () =>
    ((await run("api_start_problem")) as ApiStartProblem | null) ?? null;
}
