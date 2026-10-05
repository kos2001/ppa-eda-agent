import { readFile, readdir } from "node:fs/promises";
import path from "node:path";

// Discover actual checked-in designs. Reading this catalog never runs a flow.
export async function designExamples(designsDir) {
  const entries = await readdir(designsDir, { withFileTypes: true });
  const examples = await Promise.all(entries.filter((entry) => entry.isDirectory()).map(async (entry) => {
    const dir = path.join(designsDir, entry.name);
    let config;
    try { config = JSON.parse(await readFile(path.join(dir, "config.json"), "utf8")); }
    catch (error) { if (error.code === "ENOENT") return null; throw error; }
    let spec = null;
    try { spec = JSON.parse(await readFile(path.join(dir, "run_spec.json"), "utf8")); }
    catch (error) { if (error.code !== "ENOENT") throw error; }
    return {
      design: entry.name,
      top: config.DESIGN_NAME ?? null,
      clock_period_ns: config.CLOCK_PERIOD ?? null,
      objective: spec?.search?.objective ?? null,
      expected_outcome: spec?.expected_outcome ?? null,
      runnable: spec !== null,
      config_path: `pipeline/designs/${entry.name}/config.json`,
    };
  }));
  return examples.filter(Boolean).sort((a, b) => a.design.localeCompare(b.design));
}
