# PPA frontend

Independent React + TypeScript + Vite application. It calls HTTP/JSON and
streaming APIs; filesystem access, EDA execution and private model credentials
belong to `../server/` and `../pipeline/`. Node 22.12+ is the shared development
baseline. The backend has no npm dependencies.

From the repository root, start two terminals:

```sh
npm --prefix server start
```

```sh
npm --prefix dashboard ci
npm --prefix dashboard run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

Open http://127.0.0.1:5173. The API is at http://127.0.0.1:8123;
`GET /health` checks API availability without launching an EDA tool.

Copy `dashboard/.env.example` to `dashboard/.env.local` to change public
endpoints. Restart Vite after editing. Vite embeds `VITE_*` values into the
frontend build; keys belong in the repository-root `.env` read by the backend.
Existing server-side gateway proxy and optional browser-pasted-key fallback
remain available.

For different ports, from the repository root:

```sh
PPA_EDA_SERVER_PORT=8124 PPA_EDA_FRONTEND_ORIGINS=http://127.0.0.1:5174 npm --prefix server start
```

```sh
VITE_API_BASE_URL=http://127.0.0.1:8124 npm --prefix dashboard run dev -- --host 127.0.0.1 --port 5174 --strictPort
```

All frontend consumers share `src/api/config.ts`, including layout images,
streaming requests, status checks, data lineage and the console transcript.
No page needs to know a fixed backend port.

If edits on an external/network drive are not reflected in development, start
Vite with `PPA_EDA_WATCH_POLLING=1`. This opts into polling at 500 ms; native
watching remains the default. See [Vite watcher options](https://vite.dev/config/server-options#server-watch).

## Static build

```sh
VITE_API_BASE_URL=http://127.0.0.1:8123 npm --prefix dashboard run build
npm --prefix dashboard run preview -- --host 127.0.0.1 --port 4173 --strictPort
```

`dist/` can be served by an independent static server. The backend remains a
loopback service. For a same-origin reverse proxy, build with
`VITE_API_BASE_URL=/api`; forward `/api/*` to `127.0.0.1:8123/*`, stripping
`/api` and preserving streaming responses and image content types. It serves
no frontend assets. Custom frontend origins use an exact comma-separated
`PPA_EDA_FRONTEND_ORIGINS` allowlist; by default HTTP localhost development
ports are allowed. A remote browser needs a tunnel or an authenticated proxy
to reach the local execution service.

## Screens

The initial Overview · Examples screen discovers real configurations through
`GET /examples` and measured history through `GET /reference-db`. It includes
layout previews labelled with their source case time, candidate verification
distribution, searchable circuit examples and links to external EDA UI
references. Selecting a recorded design opens its newest pipeline case.
Configuration-only examples show that no measurements exist. Browsing never
launches experiments.

The run workspace applies patterns from SiliconCompiler, OpenROAD Web Viewer
and LanEx: select up to six recorded candidates, inspect area/power observations
and a per-check evidence matrix, export selected JSON, then open native source
reports with matching metrics highlighted. Historical-case navigation opens the
selected case. Missing source compatibility blocks ranking claims; absent run
directories are shown explicitly. Geometry loads only when the layout tab opens.
The DEF viewer supports zoom, pan, layer toggles, instance/net search and a
16×16 cell-footprint map. See [the applied patterns](../docs/eda-visualization-patterns-20261004.md).

Candidate verification includes recorded check completeness and model coverage.
Physical checks alone do not establish model qualification or functional
equivalence. Missing verdicts/checks stay unknown; failed flows and measured
violations have separate counts. Historical physical passes do not imply the
latest run passed. SRAM model audits remain visible on individual cases.

The evaluation panel reads `GET /evaluation-report` for actual timing coverage,
stage costs and source-compatible Pareto groups. Missing historical timing and
provenance stay unknown. Budget-deferred candidates are shown as `NOT EVALUATED`,
with any completed screening preserved; they are excluded from tool-failure
counts. See [the evaluation contract](../docs/evaluation-harness-20261004.md).

Other screens: Layout Pipeline, Schematic, Progress, System Health, Data & RAG,
Ask, Manual and Diagnosis. See
[`../docs/frontend-backend-and-examples-20261004.md`](../docs/frontend-backend-and-examples-20261004.md)
for architecture, circuit inventory and external reference comparisons.

Schematic opens a circuit review workspace with native xschem symbols/wires,
paper/dark canvas, selection, pan/zoom, a device/net inspector, D/G/S/B and
symbolic sizing, source attributes, and implicit standard-cell power/well
bindings. Follow a local child sheet or open a SKY130 HD gate's underlying CDL
transistors, then return to its parent. Source and review JSON can be downloaded;
the original SVG remains available separately. The inverter and NAND sheets use
connected supply rails and conventional CMOS stacks. Geometry review notes are
not native ERC/LVS/signoff. Imported MOS standard cells now receive connected
layouts only after native before/after netlists match; preserved well diagnostics
remain in Review. Large drawings open at readable text size; Fit sheet shows all.
Full screen / Expand canvas fills the viewport, initially hides the inspector,
and retains zoom, fit and selection. Show inspector restores the panel; Escape
closes fullscreen. Analysis text toggles code boxes without editing the source.
See [the source and validation contract](../docs/schematic-review-20261004.md).

Validation: `npm --prefix dashboard run build`, `npm --prefix dashboard run lint`
and the root Python test suite.
