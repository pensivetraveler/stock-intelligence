/**
 * The resident runner's LaunchAgent, through the SIW Agent shell (Tauri commands).
 * Outside Tauri (a browser, the Research app) there is no control and every call
 * reports that.
 */
export interface RunnerServiceState {
  installed: boolean;
  running: boolean;
  detail: string;
}

export interface RunnerControl {
  available: boolean;
  state(): Promise<RunnerServiceState>;
  install(): Promise<RunnerServiceState>;
  stop(): Promise<RunnerServiceState>;
  uninstall(): Promise<RunnerServiceState>;
}

type Invoke = (command: string) => Promise<unknown>;

function inTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

export function createRunnerControl(invoke?: Invoke): RunnerControl {
  const available = invoke !== undefined || inTauri();
  const call = async (command: string): Promise<RunnerServiceState> => {
    if (!available) {
      throw new Error("runner control needs the SIW Agent app");
    }
    const run =
      invoke ??
      (async (name: string) => {
        const core = await import("@tauri-apps/api/core");
        return core.invoke(name);
      });
    return (await run(command)) as RunnerServiceState;
  };
  return {
    available,
    state: () => call("runner_state"),
    install: () => call("runner_install"),
    stop: () => call("runner_stop"),
    uninstall: () => call("runner_uninstall"),
  };
}
