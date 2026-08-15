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
