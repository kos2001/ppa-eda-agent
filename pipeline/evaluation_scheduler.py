"""Opt-in measured-cost queue ordering, without predicting PPA or pruning."""
from evaluation_provenance import digest


def order(candidates, design, toolchain, cohorts, inputs, verify_fn=False):
    scored = []
    for index, candidate in enumerate(candidates):
        context = {"design": design, "pdk": candidate.get("pdk"), "scl": candidate.get("scl"),
                   "image": toolchain.get("openlane_image"), "host": toolchain.get("host"),
                   "flow": candidate.get("flow"), "status": "completed",
                   "verification_requested": bool(verify_fn),
                   "input_key": digest(inputs) if inputs.get("complete") else None,
                   "fidelity": "full_flow"}
        matches = [row for row in cohorts if inputs.get("complete") and row["context"] == context and row["samples"] >= 3]
        cost = matches[0]["median_seconds"] if matches else None
        scored.append((index, candidate, cost, matches[0]["samples"] if matches else 0))
    # Keep one unmeasured technology arm first rather than starving every
    # unseen arm behind known cheap designs. The rest stay in declared order.
    unknown = [row for row in scored if row[2] is None]
    known = sorted((row for row in scored if row[2] is not None), key=lambda row: (row[2], row[0]))
    queue = unknown[:1] + known + unknown[1:]
    return [{**candidate, "scheduling": {"policy": "measured_cost", "queue_rank": rank,
              "declared_index": index, "estimated_seconds": cost, "cost_samples": samples,
              "scope": "same design/requested technology/image/host; full-flow timings, not PPA predictions"}}
            for rank, (index, candidate, cost, samples) in enumerate(queue)]
