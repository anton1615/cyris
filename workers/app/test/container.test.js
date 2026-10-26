import { describe, it, expect } from "vitest";
import { Lifecycle, PORT } from "../src/container.js";

// A stand-in for ctx.container that answers like the runtime's: start() throws
// on a running container, and the port refuses until `refusals` run out.
function fakeContext({ running = false, refusals = 0 } = {}) {
  const container = {
    running,
    starts: [],
    timeouts: [],
    pings: [],
    forwarded: [],
    start(options) {
      if (this.running) throw new Error("container is already running");
      this.running = true;
      this.starts.push(options);
    },
    async setInactivityTimeout(ms) {
      this.timeouts.push(ms);
    },
    monitor: () => new Promise(() => {}),
    getTcpPort(port) {
      return {
        fetch: async (url, init) => {
          if (url === "http://ping") {
            container.pings.push(port);
            if (refusals-- > 0) throw new Error("connection refused");
            return new Response("not found", { status: 404 });
          }
          container.forwarded.push({ port, url, init });
          return new Response("ok");
        },
      };
    },
  };
  return { container, waited: [], waitUntil(promise) { this.waited.push(promise); } };
}

const ENV = { CYRIS_ROLE: "run" };

describe("start", () => {
  it("starts a stopped container with internet access and sets its inactivity timeout", async () => {
    const ctx = fakeContext();
    expect(await new Lifecycle(ctx, 900_000).start(ENV)).toBe(true);
    expect(ctx.container.starts).toEqual([{ env: ENV, enableInternet: true }]);
    expect(ctx.container.timeouts).toEqual([900_000]);
    expect(ctx.waited).toHaveLength(1);
  });

  it("leaves a running container alone instead of throwing", async () => {
    const ctx = fakeContext({ running: true });
    expect(await new Lifecycle(ctx, 900_000).start(ENV)).toBe(false);
    expect(ctx.container.starts).toEqual([]);
  });

  it("starts once when two triggers arrive together", async () => {
    const ctx = fakeContext();
    const lifecycle = new Lifecycle(ctx, 900_000);
    await Promise.all([lifecycle.start(ENV), lifecycle.start(ENV)]);
    expect(ctx.container.starts).toHaveLength(1);
  });

  it("starts again once the container has exited", async () => {
    const ctx = fakeContext();
    const lifecycle = new Lifecycle(ctx, 900_000);
    await lifecycle.start(ENV);
    ctx.container.running = false;
    expect(await lifecycle.start(ENV)).toBe(true);
    expect(ctx.container.starts).toHaveLength(2);
  });
});

describe("adopt", () => {
  it("sets the timeout and watches a container a restarted object finds running", async () => {
    const ctx = fakeContext({ running: true });
    await new Lifecycle(ctx, 300_000).adopt();
    expect(ctx.container.timeouts).toEqual([300_000]);
    expect(ctx.waited).toHaveLength(1);
  });
});

describe("ready", () => {
  const timing = { deadlineMs: 50, pollMs: 1 };

  it("waits for the port to answer after a cold start", async () => {
    const ctx = fakeContext({ refusals: 3 });
    await new Lifecycle(ctx, 300_000, timing).ready(ENV);
    expect(ctx.container.pings).toEqual([PORT, PORT, PORT, PORT]);
  });

  it("gives up once the deadline passes", async () => {
    const ctx = fakeContext({ refusals: Infinity });
    await expect(new Lifecycle(ctx, 300_000, timing).ready(ENV)).rejects.toThrow("connection refused");
  });

  it("pings a container it found running once, then trusts it", async () => {
    const ctx = fakeContext({ running: true });
    const lifecycle = new Lifecycle(ctx, 300_000, timing);
    await lifecycle.ready(ENV);
    await lifecycle.ready(ENV);
    expect(ctx.container.starts).toEqual([]);
    expect(ctx.container.pings).toEqual([PORT]);
  });

  it("keeps a slow boot waiting after an earlier request gave up on it", async () => {
    const ctx = fakeContext({ refusals: 200 });
    const lifecycle = new Lifecycle(ctx, 300_000, timing);
    await expect(lifecycle.ready(ENV)).rejects.toThrow("connection refused");
    const pings = ctx.container.pings.length;
    await expect(lifecycle.ready(ENV)).rejects.toThrow("connection refused");
    expect(ctx.container.pings.length).toBeGreaterThan(pings);
  });

  it("starts again when the container stops before its port answers", async () => {
    const ctx = fakeContext({ refusals: 3 });
    const port = ctx.container.getTcpPort(PORT);
    const refuse = port.fetch;
    let crashed = false;
    ctx.container.getTcpPort = () => ({
      fetch: async (url, init) => {
        if (!crashed) {
          crashed = true;
          ctx.container.running = false;
        }
        return refuse(url, init);
      },
    });
    await new Lifecycle(ctx, 300_000, timing).ready(ENV);
    expect(ctx.container.starts).toHaveLength(2);
  });

  it("holds concurrent requests on one cold start", async () => {
    const ctx = fakeContext({ refusals: 2 });
    const lifecycle = new Lifecycle(ctx, 300_000, timing);
    await Promise.all([lifecycle.ready(ENV), lifecycle.ready(ENV)]);
    expect(ctx.container.starts).toHaveLength(1);
    expect(ctx.container.pings).toHaveLength(3);
  });
});

describe("forward", () => {
  it("sends the request to the container's port over plain http", async () => {
    const ctx = fakeContext({ running: true });
    const request = new Request("https://cyris.example/settings");
    await new Lifecycle(ctx, 300_000).forward(request);
    expect(ctx.container.forwarded).toEqual([{ port: PORT, url: "http://cyris.example/settings", init: request }]);
  });

  it("answers 502 when the container drops the request", async () => {
    const ctx = fakeContext({ running: true });
    ctx.container.getTcpPort = () => ({
      fetch: async () => {
        throw new Error("connection reset");
      },
    });
    const response = await new Lifecycle(ctx, 300_000).forward(new Request("https://cyris.example/settings"));
    expect(response.status).toBe(502);
  });
});
