#!/usr/bin/env python3
r"""After a candidate passes: try the moves that were measured to help.

orchestrate() stops at the first iteration with a passing candidate. That
is right for a design that cannot close, and it leaves value on the table
for one that does: a pass is the start of optimisation, not the end. This
adds a bounded, opt-in second phase that perturbs the winner with a short
list of moves and keeps only those that make the result strictly better.

WHICH MOVES is the part that matters, and it is not chosen by plausibility.
pnr_study.py ran every placement, routing, CTS and resizer knob that
OpenLane 2.3.10 exposes one at a time, next to perturbations that
physically should not matter (FP_CORE_UTIL +-1, CLOCK_PERIOD +-1%) to
measure how far the flow moves on its own. Of 14 knobs, the majority
changed nothing (CTS clustering, routability-driven placement, the setup
slack margin: byte-identical metrics on a design too small for them to
act) or moved the result by less than the probes did. What is in MOVES
cleared the probes in the improving direction; see MOVES[*]["evidence"].

HOW A MOVE IS ACCEPTED. A trial is the winner's configuration plus one
move, run through the same full flow and the same signoff as any other
candidate. It is kept only if it passes every signoff check, is no worse
than the incumbent beyond the flow's own run-to-run movement on any
objective pick_winner() ranks by (TOLERANCE_PCT), and is better by a
material amount on a cost objective: cell area, power or core area
(GAIN_PCT). The tolerance is not slack in the verdict: signoff is not
relaxed, only the secondary ranking quantities are compared with an
allowance, because strict Pareto dominance rejected hold-margin-0 for
moving setup slack 0.9% while it cut area 9%. OpenROAD gives the same
bytes for the same inputs, so "this exact configuration is smaller and
still signs off" is a fact about one deterministic run, not a claim about
noise. (Whether it would still be smaller for a neighbouring configuration
is a different question, which is why moves are applied to the winner only
and never carried to other designs without their own trial.)

Individually-accepted moves are then combined in one more run, because
moves interact and a combination of two good ones is not guaranteed to be
good. The combination is kept only if it beats the best single move.

Cost: len(moves) trials, in parallel, plus one if two or more were
accepted. Off unless run_spec has a "polish" block.
"""
from __future__ import annotations

from typing import Callable

# table(results) -> [(tag, {objective: value})], all objectives minimised.
Table = Callable[[list], list]
# notify(kind, info): a decision, told as it is made.
Notify = Callable[[str, dict], None]

from pareto import ParetoPoint, pick_knee

# Each move is measured, not recalled. `evidence` holds the study numbers
# it was promoted on; `risk` says what the move gives up, because every
# area win here is a margin spent.
MOVES: list[dict] = [
    {"id": "hold-margin-0",
     "overrides": {"PL_RESIZER_HOLD_SLACK_MARGIN": 0.0},
     "why": "post-CTS hold repair aims for 0.1 ns of slack beyond zero; "
            "signoff requires only >= 0, and the margin is paid for in "
            "hold buffers",
     "default": True,
     "risk": "less hold headroom (gcd: worst hold slack 0.114 -> 0.053 ns, spm: "
             "0.259 -> 0.193 ns). Signoff still fails the candidate if any "
             "corner goes negative.",
     "evidence": {
         "gcd/sky130hd": "area -9.0%, power -3.2%, repair buffers 95 -> 68, vias -4.5%",
         "spm/sky130hd": "area -8.7%, power -7.5%, repair buffers 84 -> 52, vias -6.0%",
         "spm/sky130hs @ util 55": "default margin dies at RSZ-0060; margin 0 "
                                   "passes with 32 hold buffers, 5296 um^2 "
                                   "(utilization 40 with the default margin: "
                                   "221 buffers, 6520 um^2)",
         "noise floor": "area 1.4% (gcd) / 0.6% (spm) from FP_CORE_UTIL +-1 and "
                        "CLOCK_PERIOD +-1% probes"}},
    {"id": "no-input-port-buffers",
     "overrides": {"DESIGN_REPAIR_BUFFER_INPUT_PORTS": False},
     "why": "the resizer buffers every primary input; on a small design those "
            "buffers are a large share of the cells",
     "default": False,
     "risk": "inputs drive internal loads directly. STA models the SDC's "
             "driving cell, so if the real upstream driver is weaker than the "
             "SDC says, pin slews are worse than anything signoff here can "
             "see. A design-level assumption, not a pure margin, which is why "
             "this is selected by name and never by default.",
     "evidence": {
         "gcd/sky130hd": "area -4.4%, repair buffers 95 -> 60, vias -6.6%",
         "spm/sky130hd": "area -4.6%, repair buffers 84 -> 50, vias -6.5%",
         "outputs": "the same switch for output ports went the other way "
                    "(gcd area +9.7%, spm +0.4%) and is not a move"}},
]


def plan(winner_overrides: dict, base_config: dict | None,
         moves: list[dict] | None = None) -> list[dict]:
    """The moves worth trying on this winner: those that change something.

    A move whose every setting is already in force would re-run the
    winner under another name and report the repeat as an experiment."""
    base_config = base_config or {}
    out = []
    for m in ([m for m in MOVES if m.get("default")] if moves is None else moves):
        current = {k: winner_overrides.get(k, base_config.get(k)) for k in m["overrides"]}
        if all(current[k] == v for k, v in m["overrides"].items()):
            continue
        out.append(m)
    return out


# How far a trial may be worse than the incumbent on any objective and
# still count as no worse, per objective, in percent of the incumbent's
# magnitude. Strict Pareto dominance was tried first and was too strict to
# be useful: on gcd, hold-margin-0 cut cell area 9.0% and moved setup slack
# from 1.795 to 1.78 ns (-0.9%), and "dominance" refused it for that. These
# are the size of the flow's own movement, measured by pnr_study.py's probes
# (area 0.6-1.4%, power 3%, wirelength 3%, setup slack 6%).
TOLERANCE_PCT = {"area": 2.0, "power": 5.0, "core": 2.0, "margin": 10.0}

# A move must buy at least this much on a cost objective to be worth
# keeping; anything smaller is inside the noise and not a result. Slack is
# deliberately not a gain objective: a move is polish when it makes the
# design cheaper, not when it merely changes the margin.
GAIN_PCT = 3.0
GAIN_OBJECTIVES = ("area", "power", "core")


def _rel_pct(trial: float, incumbent: float) -> float:
    return (trial - incumbent) / max(abs(incumbent), 1e-12) * 100.0


def improves(incumbent: dict, trial: dict, table: Table) -> bool:
    """True when the trial passed signoff, is no worse than the incumbent
    beyond TOLERANCE_PCT on any objective, and is better by at least
    GAIN_PCT on a cost objective.

    Both are scored together so the objective set (and the
    never-a-mixture rule for optional objectives) is common to the pair.
    Signoff is not weakened: every check still has to pass; the tolerance
    only applies to the secondary ranking quantities.
    """
    if not trial.get("verdict", {}).get("passed"):
        return False
    rows = dict(table([incumbent, trial]))
    if incumbent["tag"] not in rows or trial["tag"] not in rows:
        return False
    inc, tri = rows[incumbent["tag"]], rows[trial["tag"]]
    gained = False
    for name, inc_value in inc.items():
        rel = _rel_pct(tri[name], inc_value)
        if rel > TOLERANCE_PCT[name]:
            return False
        if name in GAIN_OBJECTIVES and rel <= -GAIN_PCT:
            gained = True
    return gained


def choose(incumbent: dict, trials: list[dict], table: Table) -> list[dict]:
    """The trials that each beat the incumbent on their own."""
    return [t for t in trials if improves(incumbent, t, table)]


def best_single(accepted: list[dict], table: Table) -> dict:
    """Among individually-accepted trials, the compromise one (knee)."""
    if len(accepted) == 1:
        return accepted[0]
    points = [ParetoPoint(key=tag, objs=tuple(o.values())) for tag, o in table(accepted)]
    tag = pick_knee(points)
    return next(t for t in accepted if t["tag"] == tag)


def polish(winner: dict, moves: list[dict], run: Callable[[list[dict]], list[dict]],
           table: Table, notify: Notify | None = None) -> tuple[dict, list[dict]]:
    """Runs the trials, then the combination, and returns
    (final_winner, every_trial_run).

    `run` takes candidates ({"tag", "overrides", ...}) and returns their
    scored results in order; it is injected so the decision logic here is
    testable without a flow. `table` maps passing results to named
    objective values (orchestrator.objective_table). `notify(kind, info)`,
    if given, is told each decision as it is made, for live observation.
    """
    say = notify or (lambda kind, info: None)
    if not moves:
        return winner, []

    def candidate(tag, overrides):
        c = {"tag": tag, "overrides": overrides, "polish": True}
        for k in ("pdk", "scl"):
            if winner.get(k):
                c[k] = winner[k]
        return c

    say("polish_start", {"winner": winner["tag"], "moves": [m["id"] for m in moves]})
    trials_in = [candidate(f"{winner['tag']}-polish-{m['id']}",
                           {**winner["overrides"], **m["overrides"]})
                 for m in moves]
    trials = run(trials_in)
    accepted = []
    for t, m in zip(trials, moves):
        t["polish"] = {"move": m["id"], "why": m["why"]}
        ok = improves(winner, t, table)
        say("polish_trial", {"tag": t["tag"], "move": m["id"], "adopted": ok,
                             "passed": bool(t.get("verdict", {}).get("passed")),
                             "deltas": deltas(winner, t, table)})
        if ok:
            accepted.append(t)
    if not accepted:
        say("polish_done", {"winner": winner["tag"], "changed": False})
        return winner, trials

    best = best_single(accepted, table)
    if len(accepted) < 2:
        say("polish_done", {"winner": best["tag"], "changed": True})
        return best, trials

    merged = {}
    for t in accepted:
        merged.update(t["overrides"])
    combo_in = candidate(f"{winner['tag']}-polish-combined", merged)
    say("polish_combine", {"moves": [t["polish"]["move"] for t in accepted]})
    combo = run([combo_in])[0]
    combo["polish"] = {"move": "combined", "why": "all individually accepted moves together"}
    trials.append(combo)
    # Judged against the best single move, not the original winner: the
    # combination has to earn its extra run.
    ok = improves(best, combo, table)
    say("polish_trial", {"tag": combo["tag"], "move": "combined", "adopted": ok,
                         "passed": bool(combo.get("verdict", {}).get("passed")),
                         "deltas": deltas(best, combo, table)})
    final = combo if ok else best
    say("polish_done", {"winner": final["tag"], "changed": True})
    return final, trials


def deltas(incumbent: dict, trial: dict, table: Table) -> dict:
    """Percent change of each objective, trial against incumbent; empty
    when the trial has no comparable numbers (a failed run)."""
    if not trial.get("verdict", {}).get("passed"):
        return {}
    rows = dict(table([incumbent, trial]))
    if incumbent["tag"] not in rows or trial["tag"] not in rows:
        return {}
    inc, tri = rows[incumbent["tag"]], rows[trial["tag"]]
    return {n: round(_rel_pct(tri[n], v), 2) for n, v in inc.items()}
