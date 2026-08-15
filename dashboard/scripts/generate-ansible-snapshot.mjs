// Reads state from the SEPARATE ~/gitspace/ppa-agent repo (Ansible PPA
// pipeline) and writes public/ansible-snapshot.json here. This dashboard
// was merged from two originally-separate projects — ppa-agent's own
// dashboard/scripts/generate-snapshot.mjs is the source of truth for the
// generation logic; keep them in sync by hand if either changes.
import { execSync } from "node:child_process";
import { existsSync, readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { load as loadYaml } from "js-yaml";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ansibleRepoRoot = path.resolve(__dirname, "..", "..", "..", "ppa-agent");

if (!existsSync(ansibleRepoRoot)) {
  console.error(
    `Expected the ppa-agent repo at ${ansibleRepoRoot} — adjust ansibleRepoRoot in this script if it lives elsewhere.`
  );
  process.exit(1);
}

function sh(cmd) {
  return execSync(cmd, { cwd: ansibleRepoRoot, encoding: "utf-8" }).trim();
}

const matrixYaml = readFileSync(
  path.join(ansibleRepoRoot, "latest_builds", "matrix.yml"),
  "utf-8"
);
const rawMatrix = loadYaml(matrixYaml);

const matrix = Object.entries(rawMatrix)
  .filter(([name]) => !name.startsWith("testing-"))
  .map(([entryName, entry]) => ({
    entryName,
    githubBranch: entry.github_branch || entryName,
    packages: entry.packages.map((p) => ({
      name: p.name,
      version_specifier_set: p.version_specifier_set,
      dists: p.dists,
    })),
  }));

const resolvedVersions = JSON.parse(
  readFileSync(
    path.join(ansibleRepoRoot, "test", "build-matrix", "resolved-versions.json"),
    "utf-8"
  )
);

const branches = sh("git branch -a")
  .split("\n")
  .map((l) => l.replace(/^\*?\s+/, "").trim())
  .filter(Boolean);

const recentCommits = sh("git log --oneline -10")
  .split("\n")
  .filter(Boolean)
  .map((line) => {
    const [hash, ...rest] = line.split(" ");
    return { hash, message: rest.join(" ") };
  });

const buildEnvConfigured = existsSync(
  path.join(ansibleRepoRoot, "docker", "build-env", "Dockerfile")
);

const snapshot = {
  generatedAt: new Date().toISOString(),
  branches,
  matrix,
  resolvedVersions,
  recentCommits,
  buildEnvConfigured,
};

const outDir = path.join(__dirname, "..", "public");
mkdirSync(outDir, { recursive: true });
writeFileSync(
  path.join(outDir, "ansible-snapshot.json"),
  JSON.stringify(snapshot, null, 2)
);

console.log(`Wrote ${path.join(outDir, "ansible-snapshot.json")} (from ${ansibleRepoRoot})`);
console.log(`  branches: ${branches.length}`);
console.log(`  matrix entries: ${matrix.length}`);
console.log(`  resolved versions: ${resolvedVersions.length}`);
