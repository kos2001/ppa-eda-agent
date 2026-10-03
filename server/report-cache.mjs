import { createHash } from "node:crypto";
import { readdir, stat } from "node:fs/promises";
import path from "node:path";

// Include every input, rather than just the newest case's timestamp:
// reviews, the index, layouts and run budgets also affect these reports.
export async function reportStoreKey(refDbDir, designsDir) {
  const stamps = [];
  async function record(file) {
    const s = await stat(file);
    stamps.push([file, s.size, s.mtimeMs, s.ctimeMs]);
  }
  async function directory(dir, accept) {
    let entries;
    try {
      entries = await readdir(dir, { withFileTypes: true });
    } catch (err) {
      if (err.code !== "ENOENT") throw err;
      return;
    }
    for (const entry of entries) {
      if (entry.isFile() && accept(entry.name)) await record(path.join(dir, entry.name));
    }
  }
  try {
    await record(path.join(refDbDir, "index.json"));
    await directory(path.join(refDbDir, "cases"), n => n.endsWith(".json"));
    await directory(path.join(refDbDir, "reviews"), () => true);
    await directory(path.join(refDbDir, "layouts"), n => n.endsWith(".png"));
    for (const entry of await readdir(designsDir, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      stamps.push(["design", entry.name]);
      await directory(path.join(designsDir, entry.name), n => n === "run_spec.json");
    }
    stamps.sort((a, b) => a[0].localeCompare(b[0]) || String(a[1]).localeCompare(String(b[1])));
    return createHash("sha256").update(JSON.stringify(stamps)).digest("hex");
  } catch {
    // An unreadable/changing store must never reuse a possibly stale report.
    return null;
  }
}

export function createReportCache(getKey) {
  const entries = new Map();
  return async (route, produce) => {
    const key = await getKey();
    const hit = entries.get(route);
    if (key !== null && hit?.key === key) return hit.body;
    // Share the promise: concurrent page loads otherwise launch duplicate
    // Python processes, each parsing the whole store.
    const entry = { key, body: Promise.resolve().then(produce) };
    if (key !== null) entries.set(route, entry);
    try {
      return await entry.body;
    } catch (err) {
      if (entries.get(route) === entry) entries.delete(route);
      throw err;
    }
  };
}
