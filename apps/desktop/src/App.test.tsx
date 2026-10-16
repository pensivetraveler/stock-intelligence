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

  it("ignores a check from a replaced endpoint and stops its polling", async () => {
    vi.useFakeTimers();
    let resolveOld: (response: Response) => void = () => undefined;
    const fetcher = vi.fn<typeof fetch>((input) =>
      String(input).startsWith("http://old")
        ? new Promise<Response>((resolve) => {
            resolveOld = resolve;
          })
        : Promise.resolve(healthResponse(503)),
    );
    const { rerender } = render(
      <App apiBaseUrl="http://old" fetcher={fetcher} retryMs={100} />,
    );
    rerender(<App apiBaseUrl="http://new" fetcher={fetcher} retryMs={100} />);
    await act(async () => Promise.resolve());
    expect(screen.getByText("HTTP 503")).toBeInTheDocument();

    await act(async () => {
      resolveOld(healthResponse());
      await vi.advanceTimersByTimeAsync(50);
    });
    expect(screen.getByText("HTTP 503")).toBeInTheDocument();
    expect(screen.queryByText("API is available")).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(350);
    });
    expect(screen.getByText("disconnected")).toBeInTheDocument();
    const oldCalls = fetcher.mock.calls.filter(([input]) =>
      String(input).startsWith("http://old"),
    );
    expect(oldCalls).toHaveLength(1);
  });

  it("shows the desktop package version", async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(healthResponse());
    render(<App fetcher={fetcher} retryMs={60_000} />);

    expect(await screen.findByText("0.4.0-dev")).toBeInTheDocument();
  });
});
