// Read recorded EDA outputs only; request parameters never name a host path.
import { open, readdir, realpath, stat } from "node:fs/promises";
import path from "node:path";

const TEXT_LIMIT = 200_000;
const MAX_FILES = 2000;
const allowedName = name => /\.(rpt|log)$/i.test(name)
  || ["metrics.json", "resolved.json", "state_in.json", "state_out.json"].includes(name);
const inside = (root, file) => file === root || file.startsWith(root + path.sep);

async function recordedRoot(candidate, workspaceRoot) {
  if (!candidate.run_dir || candidate.not_evaluated) return null;
  const requested = path.resolve(candidate.run_dir);
  const root = await realpath(requested).catch(() => null);
  if (!root) return null;
  const designs = await realpath(path.join(workspaceRoot, "pipeline", "designs")).catch(() => null);
  const local = designs && inside(designs, root) && root.includes(path.sep + "runs" + path.sep);
  const ownedScratch = /^\/private\/tmp\/ppa-[a-z0-9-]+\/.+\/runs\/[^/]+$/i.test(root);
  return local || ownedScratch ? root : null;
}

export async function artifactInventory(candidate, { workspaceRoot }) {
  const root = await recordedRoot(candidate, workspaceRoot);
  if (!root) return { run_available: false, reason: "Recorded run directory unavailable", steps: [], files: [] };
  const files = [];
  let limited = false;
  async function scan(directory, step, depth) {
    for (const entry of (await readdir(directory, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name))) {
      if (files.length >= MAX_FILES) { limited = true; return; }
      if (entry.isSymbolicLink()) continue;
      const file = path.join(directory, entry.name);
      if (entry.isDirectory() && depth > 0) await scan(file, step, depth - 1);
      if (!entry.isFile() || !allowedName(entry.name)) continue;
      const resolved = await realpath(file).catch(() => null);
      if (!resolved || !inside(root, resolved)) continue;
      const info = await stat(resolved);
      files.push({ id: path.relative(root, file).split(path.sep).join("/"), step, bytes: info.size,
        modified_at: info.mtime.toISOString() });
    }
  }
  await scan(root, "run", 0);
  const steps = [];
  for (const entry of (await readdir(root, { withFileTypes: true })).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }))) {
    if (!entry.isDirectory() || !(/^[0-9]+-/.test(entry.name) || entry.name === "final")) continue;
    await scan(path.join(root, entry.name), entry.name, 2);
    const ownFiles = files.filter(file => file.step === entry.name);
    steps.push({ id: entry.name, files: ownFiles.length,
      snapshot_recorded: ownFiles.some(file => file.id === `${entry.name}/state_out.json`) });
  }
  return { run_available: true, steps, files, limited };
}

export async function readArtifact(candidate, id, options) {
  const inventory = await artifactInventory(candidate, options);
  const entry = inventory.files.find(file => file.id === id);
  if (!entry) return null;
  const root = await recordedRoot(candidate, options.workspaceRoot);
  if (!root) return null;
  const source = await realpath(path.join(root, entry.id)).catch(() => null);
  if (!source || !inside(root, source)) return null;
  const handle = await open(source, "r");
  try {
    const info = await handle.stat();
    const buffer = Buffer.alloc(Math.min(info.size, TEXT_LIMIT));
    const { bytesRead } = await handle.read(buffer, 0, buffer.length, 0);
    return { ...entry, bytes: info.size, modified_at: info.mtime.toISOString(), content: buffer.subarray(0, bytesRead).toString("utf8"),
      truncated: info.size > bytesRead, limit_bytes: TEXT_LIMIT };
  } finally { await handle.close(); }
}
