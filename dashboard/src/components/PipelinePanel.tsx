import { useEffect, useState } from "react";
import type { AnsiblePpaSnapshot } from "../ansiblePpaTypes";
import "./Tabs.css";

export default function PipelinePanel() {
  const [snapshot, setSnapshot] = useState<AnsiblePpaSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/ansible-snapshot.json")
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
        return r.json();
      })
      .then(setSnapshot)
      .catch((e) => setError(String(e)));
  }, []);

  if (error) {
    return (
      <div className="tab">
        <div className="tab__error">
          Failed to load ansible-snapshot.json: {error}. Run{" "}
          <code>npm run snapshot:ansible</code> in <code>dashboard/</code>.
        </div>
      </div>
    );
  }

  if (!snapshot) {
    return (
      <div className="tab">
        <div className="panel">
          <div className="panel__body">Loading snapshot…</div>
        </div>
      </div>
    );
  }

  return (
    <div className="tab">
      <div className="panel">
        <span className="panel__title">ansible-community/ppa — pipeline snapshot</span>
        <div className="panel__body">
          <div className="tab__meta">
            <span>
              <span className="tab__meta-label">Generated</span>
              {new Date(snapshot.generatedAt).toLocaleString()}
            </span>
            <span>
              <span className="tab__meta-label">Build env</span>
              <span
                className={
                  snapshot.buildEnvConfigured
                    ? "pill pill--good"
                    : "pill pill--critical"
                }
              >
                {snapshot.buildEnvConfigured ? "configured" : "missing"}
              </span>
            </span>
          </div>
        </div>
      </div>

      <div className="panel">
        <span className="panel__title">build matrix</span>
        <div className="panel__body">
          <table className="matrix-table">
            <thead>
              <tr>
                <th>Branch</th>
                <th>Package</th>
                <th>Version range</th>
                <th>Resolved test version</th>
                <th>Dists</th>
              </tr>
            </thead>
            <tbody>
              {snapshot.matrix.flatMap((entry) =>
                entry.packages.map((pkg) => {
                  const resolved = snapshot.resolvedVersions.find(
                    (r) =>
                      r.matrix_entry === entry.entryName &&
                      r.package === pkg.name
                  );
                  return (
                    <tr key={`${entry.entryName}-${pkg.name}`}>
                      <td>{entry.githubBranch}</td>
                      <td>{pkg.name}</td>
                      <td>{pkg.version_specifier_set}</td>
                      <td>{resolved?.resolved_version ?? "—"}</td>
                      <td>{pkg.dists.join(", ")}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <span className="panel__title">recent commits</span>
        <div className="panel__body">
          <ul className="commit-list">
            {snapshot.recentCommits.map((c) => (
              <li key={c.hash}>
                <code>{c.hash}</code> {c.message}
              </li>
            ))}
          </ul>
        </div>
      </div>

      <div className="panel">
        <span className="panel__title">branches ({snapshot.branches.length})</span>
        <div className="panel__body">
          <div className="branch-list">
            {snapshot.branches.map((b) => (
              <span key={b} className="branch-chip">
                {b}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
