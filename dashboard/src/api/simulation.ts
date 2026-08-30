export const SIM_SERVER_URL = "http://127.0.0.1:8123/simulate";

export async function runSimulation(period: number): Promise<string> {
  const res = await fetch(SIM_SERVER_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ period }),
  });
  const data = await res.json();
  if (!res.ok) {
    throw new Error(data?.error ?? `${res.status} ${res.statusText}`);
  }
  return data.output;
}

/** The TCL the server will run, read from its own template file.
 *
 * Fetched rather than written out here so the page cannot show a script
 * that differs from the one that executes. The period is substituted
 * client-side; if that token ever changes, the page renders the literal
 * `{{PERIOD}}` — visibly wrong rather than quietly wrong.
 */
export async function fetchSimScript(): Promise<string> {
  const res = await fetch("http://127.0.0.1:8123/simulate/script");
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  const data = await res.json();
  return data.template as string;
}
