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
- `GET /reference-db`: measured cases, with conditional `?since=` refresh.
- `GET /reference-db/candidate?file=…&tag=…`: deferred layout/netlist detail.
- `GET /reference-db/layouts/<name>.png`: stored layout image.
- `GET /gateway-status`, `GET /toolchain-status`: existing model/tool status probes.
- `GET /analog/cells`: checked-in and generated schematic inventory.

Existing pipeline/run, review, diagnosis, ask, translation and streaming API
contracts are preserved. The backend never serves `dashboard/dist/`. Local EDA
execution and case storage stay on this side of the HTTP boundary.
