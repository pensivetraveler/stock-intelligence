export type ApiConnectionState = "checking" | "connected" | "disconnected";

export interface HealthResult {
  state: ApiConnectionState;
  message: string;
}

export async function checkApiHealth(
  apiBaseUrl: string,
  fetcher: typeof fetch,
  timeoutMs: number,
): Promise<HealthResult> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetcher(`${apiBaseUrl}/health`, {
      signal: controller.signal,
    });
    if (!response.ok) {
      return { state: "disconnected", message: `HTTP ${response.status}` };
    }
    const payload: unknown = await response.json();
    if (
      typeof payload !== "object" ||
      payload === null ||
      !("status" in payload) ||
      payload.status !== "ok"
    ) {
      return { state: "disconnected", message: "Invalid health response" };
    }
    return { state: "connected", message: "API is available" };
  } catch (error) {
    const message = error instanceof Error ? error.message : "Network error";
    return { state: "disconnected", message };
  } finally {
    window.clearTimeout(timeout);
  }
}
