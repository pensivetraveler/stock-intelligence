import { describe, expect, it, vi } from "vitest";

import { ChartSyncGroup } from "./chart-sync";

describe("chart sync group", () => {
  it("forwards a range to the other charts only", () => {
    const group = new ChartSyncGroup();
    const first = vi.fn();
    const second = vi.fn();
    group.register("a", first);
    group.register("b", second);

    group.publish("a", { from: 1, to: 2 });

    expect(first).not.toHaveBeenCalled();
    expect(second).toHaveBeenCalledWith({ from: 1, to: 2 });
  });

  it("does not echo a range back while forwarding it", () => {
    const group = new ChartSyncGroup();
    const echoes = vi.fn();
    group.register("a", echoes);
    group.register("b", (range) => group.publish("b", range));

    group.publish("a", { from: 1, to: 2 });

    expect(echoes).not.toHaveBeenCalled();
  });

  it("does nothing while disabled and forgets unregistered charts", () => {
    const group = new ChartSyncGroup(false);
    const listener = vi.fn();
    const unregister = group.register("b", listener);

    group.publish("a", { from: 1, to: 2 });
    group.enabled = true;
    unregister();
    group.publish("a", { from: 1, to: 2 });

    expect(listener).not.toHaveBeenCalled();
  });
});
