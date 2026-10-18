"""Thread-safe admission limits shared by screening, flows and polishing.

A deadline stops new evaluations. Work already admitted completes with its
verification; it is never killed or labelled PASS because its time ran out.
"""
import math
import threading
import time


def validate_limits(spec):
    if spec is None:
        return None
    if not isinstance(spec, dict) or not spec:
        raise ValueError("evaluation_budget must be a non-empty object")
    if set(spec) - {"max_evaluations", "max_wall_seconds"}:
        raise ValueError("unknown evaluation_budget field")
    count = spec.get("max_evaluations")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1):
        raise ValueError("max_evaluations must be a positive integer")
    seconds = spec.get("max_wall_seconds")
    if seconds is not None and (isinstance(seconds, bool) or not isinstance(seconds, (int, float))
                                or not math.isfinite(seconds) or seconds <= 0):
        raise ValueError("max_wall_seconds must be positive and finite")
    if count is None and seconds is None:
        raise ValueError("evaluation_budget needs at least one limit")
    return dict(spec)


class EvaluationBudget:
    def __init__(self, spec, clock=time.monotonic):
        self.limits = validate_limits(spec)
        self.clock = clock
        self.started = clock()
        self.lock = threading.Lock()
        self.evaluations = []
        self.denied = []

    def start(self, kind, tag):
        with self.lock:
            elapsed = self.clock() - self.started
            reason = None
            if len(self.evaluations) >= self.limits.get("max_evaluations", float("inf")):
                reason = "max_evaluations"
            elif elapsed >= self.limits.get("max_wall_seconds", float("inf")):
                reason = "max_wall_seconds"
            if reason:
                self.denied.append({"kind": kind, "tag": tag, "reason": reason})
                return None
            ticket = {"kind": kind, "tag": tag, "started_seconds": elapsed, "status": "running"}
            self.evaluations.append(ticket)
            return ticket

    def finish(self, ticket, status):
        with self.lock:
            ticket.update(status=status, seconds=round(self.clock() - self.started - ticket["started_seconds"], 6))

    def snapshot(self):
        with self.lock:
            return {"limits": self.limits, "started_evaluations": len(self.evaluations),
                    "elapsed_seconds": round(self.clock() - self.started, 6),
                    "evaluations": [dict(row) for row in self.evaluations],
                    "not_evaluated": [dict(row) for row in self.denied],
                    "policy": "stop new admissions; complete admitted verification"}


def deferred(candidate, budget):
    reason = next(row["reason"] for row in reversed(budget.snapshot()["not_evaluated"]) if row["tag"] == candidate["tag"])
    return {"tag": candidate["tag"], "overrides": candidate.get("overrides", {}),
            "pdk": candidate.get("pdk"), "scl": candidate.get("scl"),
            "not_evaluated": True, "budget_exhausted": reason,
            "evaluation_fidelity": "screen" if "screen_evaluation" in candidate else "not_evaluated",
            **({"scheduling": candidate["scheduling"]} if "scheduling" in candidate else {}),
            **({"screen_evaluation": candidate["screen_evaluation"]} if "screen_evaluation" in candidate else {})}
