import { randomUUID } from "node:crypto";

// One preparation task and one render per cell/view at a time. Polling does not
// start new work; completed jobs expire to keep the process memory bounded.
export function createSchematicJobs({ prepare, render, now = Date.now, ttl = 600_000, maxJobs = 128 }) {
  const jobs = new Map(), active = new Map();
  let preparation;
  let renderQueue = Promise.resolve();
  function ensurePrepared() {
    if (!preparation) {
      preparation = Promise.resolve().then(prepare).finally(() => { preparation = null; });
    }
    return preparation;
  }
  function prune() {
    for (const [id, job] of jobs) {
      if (job.finishedAt !== undefined && now() - job.finishedAt >= ttl) jobs.delete(id);
    }
    for (const [id, job] of jobs) {
      if (jobs.size < maxJobs) break;
      if (job.finishedAt !== undefined) jobs.delete(id);
    }
  }
  function start(cell, circuitOnly, prepareOnly = false) {
    const key = JSON.stringify([cell, circuitOnly, prepareOnly]);
    if (active.has(key)) return active.get(key);
    prune();
    if (jobs.size >= maxJobs) throw new Error("Renderer is busy; retry shortly");
    const job = { id: randomUUID(), state: "preparing", cell, circuitOnly };
    jobs.set(job.id, job);
    active.set(key, job);
    job.done = (async () => {
      try {
        await ensurePrepared();
        if (!prepareOnly) {
          job.state = "queued";
          const work = renderQueue.then(async () => {
            job.state = "rendering";
            return render(cell, circuitOnly);
          });
          renderQueue = work.catch(() => {});
          job.result = await work;
        }
        job.state = "ready";
      } catch (error) {
        job.state = "failed";
        job.error = String(error.message ?? error);
        job.reason = error.reason ?? "render_failed";
      } finally {
        job.finishedAt = now();
        active.delete(key);
      }
    })();
    return job;
  }
  return { start, get(id) { prune(); return jobs.get(id); } };
}
