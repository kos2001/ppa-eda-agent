# PPA backend API

Standalone Node HTTP service; no framework or npm dependencies. From the repo
root run `npm --prefix server start` or `npm --prefix server run dev` (watch).
The frontend is built and served separately from `../dashboard/`.

The server reads the root `.env` before resolving its configuration:

| Variable | Default | Purpose |
| --- | --- | --- |
| `PPA_EDA_SERVER_PORT` | `8123` | API port, integer 1–65535 |
| `PPA_EDA_FRONTEND_ORIGINS` | HTTP localhost/127.0.0.1 ports | Optional comma-separated frontend-origin allowlist |
| `PPA_EDA_GATEWAY_BASE_URL` | `http://127.0.0.1:8700` | Server-side Hermes gateway endpoint |
| `PPA_EDA_GATEWAY_KEY` | unset | Private gateway key; never included in a frontend build |

It binds `127.0.0.1`. Paths to `pipeline/` and `reference-db/` are resolved
relative to this module, so starting from `server/` or the root is equivalent.

Core read endpoints:

- `GET /health`: API liveness (`status`, `service`, `api_version`); no claim of EDA tool readiness.
- `GET /examples`: discovers design `config.json` and `run_spec.json`, without executing them.
- `GET /evaluation-report`: measured timing coverage, stage costs and source-compatible Pareto groups; refreshes a derived cache without EDA execution.
- `GET /reference-db`: measured cases, with conditional `?since=` refresh.
- `GET /reference-db/candidate?file=…&tag=…`: deferred layout/netlist detail.
- `GET /reference-db/artifacts?file=…&tag=…`: available native text reports and recorded step snapshots. Add `&id=…` from that inventory to read an excerpt, limited to 200 KB. No arbitrary host paths; absent run files remain unavailable.
- `GET /reference-db/layouts/<name>.png`: stored layout image.
- `GET /gateway-status`, `GET /toolchain-status`: existing model/tool status probes.
- `GET /analog/cells`: checked-in and generated schematic inventory.
- `GET /analog/inspect?cell=<analog|gate>/<design>/<cell>`: read-only source,
  symbol hashes, geometric pin/net preview, hierarchy and review notes; no EDA
  execution. Restricted source roots, an 8,000-element cap, no Tcl evaluation.
  This does not produce native ERC/LVS or expand bus expressions.
- `GET /analog/svg?cell=…`: starts a shared background drawing task (HTTP 202).
  Poll the returned relative `poll` URL until it returns HTTP 200 with native
  SVG, or an error with a readable `error` and machine-readable `reason`.
  `&view=circuit` caches a separate `.circuit.svg` with analysis/code boxes
  removed from the transient drawing canvas. Default / `view=sheet` returns the
  full sheet. Rendering never saves that canvas or changes the `.sch` source.
  Inspection includes matching layout provenance and preserved native netlisting
  diagnostics for re-drafted standard cells; source/symbol changes invalidate it.

Existing pipeline/run, review, diagnosis, ask, translation and streaming API
contracts are preserved. The backend never serves `dashboard/dist/`. Local EDA
execution and case storage stay on this side of the HTTP boundary.

### Windows schematic rendering

When Windows has neither xschem nor Docker on PATH, SVG generation uses Python 3
and the renderer in the default WSL distribution. Set
`CUSTOM_BRIDGE_WSL_DISTRO=Ubuntu` to select a distribution explicitly. Docker must
be running and accessible to that distribution's user. The backend converts
Windows paths with `wslpath`; PDK mounts use the resolved variant directory so
Windows junctions do not become broken links inside the container.

Image preparation is separate from rendering and shared by concurrent requests.
Tasks report `preparing`, `queued`, and `rendering` before completion; the UI
shows these phases and offers retry on failure. `POST /analog/renderer/prepare`
starts preparation alone and returns a polling URL (ready means HTTP 200 JSON).
Rendering tasks return HTTP 200 SVG instead. Completed tasks expire after ten
minutes and the server retains at most 128 task records. Drawings are serialized
and concurrent requests for the same cell/view share a task.

The prepared image is `ppa-eda/xschem:3.4.4-1-r1`. Its base image digest and xschem
package version (`3.4.4-1`) are fixed in the Dockerfile, with a build-time version
assertion. Other Debian dependency packages are not snapshot-pinned. To prepare
the image before serving requests, run this from the repository root:

```powershell
python pipeline/schematic_service.py prepare
wsl -d Ubuntu --exec docker run --rm ppa-eda/xschem:3.4.4-1-r1 xschem --version
```

Only the Dockerfile is sent to the builder, with no repository/PDK build context.
The image is local to the WSL Docker engine; pushing source to GitHub does not
publish it to a container registry. First-use builds need network access. They
run in a background task with their own deadline rather than blocking an HTTP
request; individual polling requests return immediately.
Rendering failures return the renderer's reason rather than attempting to read
a missing SVG. Source schematics are never rewritten by the drawing operation.
New output is generated in a temporary directory, checked for renderer failure
and valid SVG dimensions, and atomically replaces the previous drawing only on
success. Cache sidecars hash the source, symbol/configuration libraries,
renderer code, Dockerfile, actual image ID (or local executable), view mode and
output bytes. Changing inputs during rendering rejects the result for retry.
