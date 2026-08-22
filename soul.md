# soul.md

What this project actually *is*, underneath the components — for anyone
(human or agent) picking this up cold. Not a spec, not a status report;
those live in `docs/superpowers/specs/` and `reference-db/`. This is the
handful of commitments that shouldn't drift as the code does.

## What this is

An autonomous chip-layout agent pipeline: real RTL goes in, real OpenLane2
placement/routing candidates come out, scored against real signoff data
(DRC/LVS/timing/power from actual tools, not estimates). Not a simulator
of that process — every number in `reference-db/` came from a real Docker
run of real EDA tools against a real PDK.

A DTCO (design-technology co-optimization) agent, not a report viewer.
The dashboard's job is to be the agent's control surface — trigger a real
run, watch it work, see what it decided and why — not to be the product
itself. If a screen only shows past results with no way to act on them,
it's drifted back into "report reader" and needs fixing (see `POST
/pipeline/run` in `server/index.mjs` and `RunAgentPanel` in
`PipelineTab.tsx` for the current shape of that control surface).

## Commitments

**Real, or say so.** No fabricated metrics, no "should pass" claims
without a run to back them, no mocked EDA output standing in for the real
thing — even under time pressure, even mid-experiment. When a real run
can't be done (no artifacts, no live credential, no infra), the honest
answer is "unverified," not a plausible-sounding guess. This has cost real
time this project's history (an entire diagnosis got rewritten after
finally checking a `.lib` file directly instead of reasoning from first
principles) and that trade was worth it every time.

**A closed door is data, not a failure to hide.** `sram_wrapper` has never
passed. That's in the README, in the dashboard, in the reference-db
diagnosis, with the actual reasoning for why forcing it further wasn't
worth it. A pipeline that only shows you its wins isn't trustworthy about
the wins either.

**Borrow the working part, not the whole machine.** AutoTuner, NSGA-II,
sign-off's SVG charts, mi-report's credential pattern — each got adopted
for the one real gap it filled, with the parts that didn't fit this
project's scale (Ray/hyperopt, a GP surrogate with no data to train on)
explicitly named and left out. Prior art earns its place by solving a
problem this project actually has, not by being impressive.

**Automate what's mechanical, escalate what's judgment.**
`propose_repairs()` only knows patterns backed by a real, observed
failure — it doesn't guess. Everything else routes through
`request_review.py`'s human-in-the-loop path: a real subagent, a real
second opinion, a real decision recorded in the case file. The line
between those two moves only when a new pattern is *proven*, never
assumed.

**The reference-db is the memory.** Every real run, every real failure,
every real diagnosis lives there — not just as a log, but as the thing
`self_improve.py`, `pareto.py`'s candidate ranking, and any future
learned component will train on. Treat it as a growing asset, not
scratch output: don't hand-edit it carelessly (a shell backtick bug once
silently ate part of a diagnosis — `request_review.py` exists so that
doesn't happen again).

## What this deliberately isn't (yet)

Not RL- or surrogate-model-driven — there isn't enough reference-db data
to train either honestly, and real runs are still cheap enough not to
need them. Not a full SRAM bitcell flow — standard-cell digital layout
first, on purpose. Not trying to cover every EDA failure mode — three
real patterns, found by hitting them for real, is the actual state, not
an aspiration rounded up.
