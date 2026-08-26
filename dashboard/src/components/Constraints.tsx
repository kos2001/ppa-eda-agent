import type { Constraints, PdkRules } from "../api/referenceDb";
import { useLang } from "../i18n";
import "./Constraints.css";

// The rules a candidate was judged against.
//
// Stage 4 is called "Physical Constraint Evaluation" and, until this
// existed, showed only the candidates that died there — a failure
// against a constraint the reader could not see anywhere in the console.
// The two conclusions this project has had to overturn were both cases
// of arguing about a limit instead of reading it.
//
// It lives inside the stage-4 artifact rather than as its own top-level
// panel on purpose. The console's measured problem has been dispersion:
// the fix that worked last time was deleting blocks, not adding a
// tenth. Constraints belong where they are applied.
//
// Split into "fixed by the process" and "chosen by us", because that
// distinction is what tells a reader whether a violation is something a
// repair could ever address. A 0.04 ns pin limit from a vendor liberty
// and a 700 um die from our own config look identical in a flat list
// and are nothing alike.

// Arrays are bracketed, not comma-joined. A run that swept DIE_AREA
// through four rectangles rendered as
// "0, 0, 8, 8, 0, 0, 16, 16, 0, 0, 32, 32, ..." — sixteen numbers with
// no visible boundary between the values.
function fmt(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return `[${v.join(", ")}]`;
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

// Blank cells are ambiguous — a reader takes them for zero. li1 really
// has no PITCH in the sky130 tech LEF, so it must read as absent.
function num(v: number | null): string {
  return v === null || v === undefined ? "—" : String(v);
}

function PdkTable({ pdk }: { pdk: PdkRules }) {
  const { t } = useLang();
  return (
    <>
      <div className="cn__facts">
        {pdk.manufacturing_grid_um !== null && (
          <span>
            <span className="tab__meta-label">{t("cn_grid")}</span>
            <code>{pdk.manufacturing_grid_um} µm</code>
          </span>
        )}
        {pdk.sites.map((s) => (
          <span key={s.name}>
            <span className="tab__meta-label">{t("cn_site")} {s.name}</span>
            <code>{s.width_um} × {s.height_um} µm</code>
          </span>
        ))}
      </div>
      <div className="cn__scroll">
        <table className="tab__summary cn__table">
          <thead>
            <tr>
              <th>{t("cn_layer")}</th>
              <th>{t("cn_dir")}</th>
              <th>{t("cn_pitch")}</th>
              <th>{t("cn_minw")}</th>
              <th>{t("cn_minsp")}</th>
              <th>{t("cn_minarea")}</th>
              <th>{t("cn_maxdens")}</th>
            </tr>
          </thead>
          <tbody>
            {pdk.routing_layers.map((l) => (
              <tr key={l.name}>
                <td><code>{l.name}</code></td>
                <td>{l.direction ?? "—"}</td>
                <td>{num(l.pitch_um)}</td>
                <td>{num(l.min_width_um)}</td>
                <td>{num(l.min_spacing_um)}</td>
                <td>{num(l.min_area_um2)}</td>
                <td>{l.max_density_pct === null ? "—" : `${l.max_density_pct}%`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="cn__src" title={pdk.source}>
        {t("cn_source")} <code>{pdk.source.replace(/^.*\/pdk\//, "pdk/")}</code>
      </p>
    </>
  );
}

export default function ConstraintsView({
  constraints,
  overridden = new Map(),
}: {
  constraints: Constraints | null | undefined;
  // Constraint key -> the values candidates actually ran with. Turns
  // "a repair may change these" from a claim into the record: in
  // counter4_tinydie the declared DIE_AREA is 8x8 µm and the winner ran
  // at 64x64, which reads as a contradiction until you can see that a
  // repair moved it.
  overridden?: Map<string, unknown[]>;
}) {
  const { t } = useLang();

  // An older case simply predates this being recorded. Saying so is
  // different from saying there are no constraints, and the difference
  // matters when the question is why a candidate failed.
  if (!constraints) return <p className="sa__none">{t("cn_not_recorded")}</p>;
  if (constraints.error)
    return <p className="sa__none">{t("cn_failed")}: {constraints.error}</p>;

  const d = constraints.design;
  const targets = Object.entries(d?.targets ?? {});

  return (
    <div className="cn">
      <section className="cn__group cn__group--ours">
        <h4>{t("cn_ours")}</h4>
        <p className="cn__hint">{t("cn_ours_hint")}</p>
        <ul className="sa__kv">
          {(d?.settings ?? []).map((s) => {
            // A candidate may pass the declared value back verbatim
            // (counter4_tinydie's baseline re-states DIE_AREA 8x8).
            // That is not a change, and striking the declared value
            // through for it would report one where none happened.
            const changed = (overridden.get(s.key) ?? []).filter(
              (v) => JSON.stringify(v) !== JSON.stringify(s.value)
            );
            const ran = changed.length ? changed.map(fmt) : undefined;
            return (
              <li key={s.key}>
                <span className="sa__k" title={s.key}>{s.label}</span>
                <span className="sa__v">
                  <span className={ran ? "cn__superseded" : undefined}>
                    {fmt(s.value)}
                  </span>
                  {ran && (
                    <span className="cn__ran">
                      {" → "}
                      {ran.join(", ")}
                      <span className="cn__ran-tag">{t("cn_by_candidate")}</span>
                    </span>
                  )}
                </span>
              </li>
            );
          })}
          {targets.map(([k, v]) => (
            <li key={k}>
              <span className="sa__k">{k.replace(/_/g, " ")}</span>
              <span className="sa__v">{fmt(v)}</span>
            </li>
          ))}
        </ul>

        {(d?.fixed_macros ?? []).length > 0 && (
          <div className="cn__macros">
            <span className="tab__meta-label">{t("cn_fixed_macros")}</span>
            <ul className="sa__kv">
              {d!.fixed_macros.map((m) => (
                <li key={m.instance}>
                  <span className="sa__k"><code>{m.instance}</code></span>
                  <span className="sa__v">
                    {m.location_um
                      ? `(${m.location_um[0]}, ${m.location_um[1]}) µm`
                      : "—"}
                    {m.orientation ? ` · ${m.orientation}` : ""}
                  </span>
                </li>
              ))}
            </ul>
            {/* A pinned macro constrains everything routed to it — this
                is the constraint that mattered most in sram_wrapper and
                was invisible in the console the whole time. */}
            <p className="cn__hint">{t("cn_macro_hint")}</p>
          </div>
        )}
      </section>

      <section className="cn__group cn__group--pdk">
        <h4>{t("cn_pdk")}</h4>
        <p className="cn__hint">{t("cn_pdk_hint")}</p>
        {constraints.pdk ? (
          <PdkTable pdk={constraints.pdk} />
        ) : (
          <p className="sa__none">
            {t("cn_pdk_missing")}
            {constraints.pdk_error ? ` (${constraints.pdk_error})` : ""}
          </p>
        )}
      </section>
    </div>
  );
}
