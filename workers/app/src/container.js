// The container's lifecycle on the Durable Object's own ctx.container, kept
// apart from index.js so a test can hand it a fake container. The Container
// class from @cloudflare/containers did all of this for us; Cloudflare stops
// maintaining it after 2026-12-31.

export const PORT = 8766;
// What the Container class allowed a cold start: 8s for an instance, then 20s
// for the port, polled every 300ms with 5s per attempt.
const READY_DEADLINE_MS = 28_000;
const POLL_MS = 300;
const PING_MS = 5_000;

const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export class Lifecycle {
  constructor(ctx, inactivityTimeoutMs, { deadlineMs = READY_DEADLINE_MS, pollMs = POLL_MS } = {}) {
    this.ctx = ctx;
    this.inactivityTimeoutMs = inactivityTimeoutMs;
    this.deadlineMs = deadlineMs;
    this.pollMs = pollMs;
  }

  // A Durable Object restart, a deploy for one, drops both the timeout and the
  // monitor, so a restarted object calls this on a container it finds running.
  adopt() {
    this.ctx.waitUntil(
      this.ctx.container.monitor().then(
        () => console.log("container stopped", { exitCode: 0 }),
        (error) => console.log("container stopped", { exitCode: error?.exitCode, reason: String(error) }),
      ).finally(() => {
        this.listening = false;
      }),
    );
    return this.ctx.container.setInactivityTimeout(this.inactivityTimeoutMs);
  }

  // start() throws on a running container, where the Container class skipped
  // it: an hourly tick landing on a manual run must not fail. Concurrent
  // callers share one start, so the second never sees a half-started one.
  start(env) {
    this.starting ??= this.#start(env).finally(() => {
      this.starting = null;
    });
    return this.starting;
  }

  async #start(env) {
    if (this.ctx.container.running) return false;
    this.ctx.container.start({ env, enableInternet: true });
    await this.adopt();
    return true;
  }

  // start() returns before the container listens, and running says nothing
  // about the port, so a container this object has not yet heard answer is
  // polled. Any HTTP answer, a 404 included, means the server is up.
  ready(env) {
    this.booting ??= this.#boot(env).finally(() => {
      this.booting = null;
    });
    return this.booting;
  }

  async #boot(env) {
    if (this.listening && this.ctx.container.running) return;
    const deadline = Date.now() + this.deadlineMs;
    for (;;) {
      // An allocation can fail after start() returns, and only monitor() is
      // told; running turning false is how this loop hears of it.
      await this.start(env);
      try {
        const response = await this.ctx.container
          .getTcpPort(PORT)
          .fetch("http://ping", { signal: AbortSignal.timeout(PING_MS) });
        await response.body?.cancel();
        this.listening = true;
        return;
      } catch (error) {
        if (Date.now() + this.pollMs >= deadline) throw error;
      }
      await pause(this.pollMs);
    }
  }

  async forward(request) {
    try {
      return await this.ctx.container.getTcpPort(PORT).fetch(request.url.replace("https:", "http:"), request);
    } catch (error) {
      console.log("container error:", error);
      return new Response(`Container dropped the request: ${error}`, { status: 502 });
    }
  }
}
