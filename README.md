# ppa-eda-agent

Semiconductor PPA (Power, Performance, Area) analysis — a Claude Code
subagent that reads Synopsys/OpenSTA EDA reports, a live OpenSTA
simulation you can actually run, and a dashboard that ties both together.

Not related to [ppa-agent](https://github.com/kos2001/ppa-agent) (Ansible
Personal Package Archive tooling) despite the shared acronym — same three
letters, completely different domain (chip design vs. Linux packaging).

## What's here

```
.claude/agents/ppa-eda-analyst.md   Claude Code subagent: diagnoses PPA
                                     issues from pasted/given report text
references/                         Report format knowledge the agent and
                                     the dashboard's parsers are built on:
  report-area.md                      Design Compiler report_area
  report-timing.md                    PrimeTime report_timing
  report-power.md                     PrimePower report_power
  see-also.md                         Real open-source examples (OpenSTA,
                                       Yosys) and documented format variants
sim/                                 A real 5-cell OpenSTA design (from
                                     OpenSTA's own examples/) used to drive
                                     live simulation
server/index.mjs                    Local server that runs that design
                                     through the openroad/opensta Docker
                                     image on demand
dashboard/                          React + Vite + TypeScript UI: paste a
                                     report and see it visualized, or run a
                                     real simulation and get a live agent
                                     diagnosis
docs/superpowers/                   Design specs and implementation plans
                                     from how this was built
```

## Dashboard

Four report-visualization tabs (Area, Timing, Power, Trade-offs) plus a
live Simulate tab and a Diagnosis page. Fully client-side for the
report-paste tabs — no backend needed. Simulate needs the local
simulation server (below); Diagnosis needs a hermes-gateway client key.

```sh
cd dashboard
npm install
npm run dev
```

### Simulate tab (real OpenSTA, not mocked)

Requires Docker. Pulls `openroad/opensta:latest` (amd64 image, runs via
emulation on Apple Silicon) on first use.

```sh
node server/index.mjs   # listens on 127.0.0.1:8123
```

Then use the Simulate tab's clock-period input and "Run simulation"
button — it runs the bundled `sim/example1.v` design through OpenSTA and
shows real timing/power results. Tightening the period below ~0.13ns
produces a genuine timing violation.

### Diagnosis page (live agent, via hermes-gateway)

The `ppa-eda-analyst` subagent is also wired up as a hermes-agent profile
so the dashboard can call it as a live, streaming diagnostic assistant —
not just a static report parser.

1. A hermes-gateway instance (local OpenAI-compatible reverse proxy in
   front of hermes-agent instances) must be running with a
   `ppa-eda-analyst` upstream registered — set up as its own hermes
   profile pointed at this repo's agent definition. This is
   environment-specific; there's no public setup doc to link here.
2. On the Diagnosis page, paste your gateway client key (from
   `GATEWAY_CLIENT_KEYS` in the gateway's `.env`) — stored only in this
   browser's `localStorage`.
3. Run a simulation on the Simulate tab, then click "Diagnose this
   result." The diagnosis streams in live (SSE), and you'll get a browser
   notification (and a badge on the Diagnosis nav tab) when it's done,
   even if you've switched to another tab.

## Status

Report parsers (`dashboard/src/parsers/`) have been validated against
real, non-synthetic report text — see `references/see-also.md` for what
was pulled from OpenSTA's own test suite and what bugs that testing
caught (the parsers originally assumed a slack-number position that turned
out to be Synopsys-specific, not universal).

`report_area` has no live-simulation path: OpenSTA is a timing/power tool
only, it doesn't do synthesis, so there's no way to generate a real
`report_area`-equivalent the way Simulate does for timing/power. The Area
tab is paste-only.
