import type { CandidateVerdict, FunctionalRegression } from "../api/referenceDb";

function RegressionStage({ label, stage }: { label: string; stage: FunctionalRegression }) {
  return <div>
    <p>{label}: {stage.rtl_status ?? stage.status}</p>
    {stage.reason && <p>{stage.reason}</p>}
    {stage.runs.map(run => (
      <p key={run.seed}>Seed {run.seed}: {run.status} · {run.checks} {run.oracle?.trace_cycles ? "trace cycles" : "checks"}
        {run.oracle && ` · oracle ${run.oracle.status}${run.oracle.checks != null ? ` (${run.oracle.checks} checked outputs)` : ""}`}
        {run.oracle?.undefined_by_port && ` · undefined outputs excluded: ${run.oracle.undefined_by_port.join(" / ")}`}
        {run.timed_out && " · timeout"}
      </p>
    ))}
  </div>;
}

export default function ImplementationEvidence({ verdict }: { verdict: CandidateVerdict }) {
  const { functional_regression: regression, implementation_evidence: evidence, target_checks: budgets } = verdict;
  return <div className="pipeline__provenance">
    <span className="tab__meta-label">Implementation evidence</span>
    {regression && <>
      <p>Functional regression: {regression.status}</p>
      <p>{regression.scope}</p>
      {regression.oracle && <p>Independent Python oracle: {regression.oracle.name}</p>}
      <RegressionStage label="RTL (Verilator)" stage={regression} />
      {regression.post_route && <RegressionStage label="Post-route (Icarus)" stage={regression.post_route} />}
    </>}
    {evidence ? <>
      <p>{evidence.required ? "Required for selection" : "Informational"}
        {" · "}{evidence.qualified ? "Local checks passed" : "Local checks incomplete or failed"}</p>
      {evidence.checks.map(check => <p key={check.name}>{check.name}: {check.status}</p>)}
      <p>{evidence.scope}</p>
    </> : <p>Not recorded for this run.</p>}
    {!!budgets?.length && <table className="pipeline__table">
      <thead><tr><th>Budget</th><th>Measured</th><th>Limit</th><th>Status</th></tr></thead>
      <tbody>{budgets.map(check => <tr key={check.target}>
        <td>{check.target}</td><td>{check.actual ?? "unverified"} {check.unit}</td>
        <td>≤ {check.limit} {check.unit}</td><td>{check.status}</td>
      </tr>)}</tbody>
    </table>}
  </div>;
}
