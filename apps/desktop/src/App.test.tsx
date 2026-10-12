import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

function healthResponse(
  status = 200,
  payload: unknown = { status: "ok" },
): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.useRealTimers();
});

describe("API connection state", () => {
  it("shows connected only for a valid successful health response", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(healthResponse());
    render(<App fetcher={fetcher} retryMs={60_000} />);

    expect(await screen.findByText("connected")).toBeInTheDocument();
    expect(screen.getByText("API is available")).toBeInTheDocument();
  });

  it("shows disconnected for a non-2xx response", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(healthResponse(503));
    render(<App fetcher={fetcher} retryMs={60_000} />);

    expect(await screen.findByText("disconnected")).toBeInTheDocument();
    expect(screen.getByText("HTTP 503")).toBeInTheDocument();
  });

  it("returns to connected on the next retry", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("Connection refused"))
      .mockResolvedValue(healthResponse());
    render(<App fetcher={fetcher} retryMs={100} />);

    await act(async () => Promise.resolve());
    expect(screen.getByText("disconnected")).toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(screen.getByText("connected")).toBeInTheDocument();
  });

  it("rejects a malformed health payload", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValue(healthResponse(200, { ready: true }));
    render(<App fetcher={fetcher} retryMs={60_000} />);

    expect(await screen.findByText("disconnected")).toBeInTheDocument();
    expect(screen.getByText("Invalid health response")).toBeInTheDocument();
  });
});
