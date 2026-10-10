"""Summarize finished aes diode-trim runs: final metrics, antenna pins, trim report."""
import glob, json, sys

def summarize(run):
    m = json.load(open(run + "/final/metrics.json"))
    r = json.load(open(run + "/resolved.json"))

    def mx(p):
        v = [x for k, x in m.items() if k.startswith(p) and "__corner:" in k]
        return max(v) if v else None

    ws = min(v for k, v in m.items() if k.startswith("timing__setup__ws__corner:"))
    hs = min(v for k, v in m.items() if k.startswith("timing__hold__ws__corner:"))
    rows = lambda p: [[c.strip() for c in l.split("│")[1:-1]] for l in open(p) if l.startswith("│")]
    last = sorted(glob.glob(run + "/[0-9]*-openroad-checkantennas*"), key=lambda d: int(d.split("/")[-1].split("-")[0]))[-1]
    ant = rows(last + "/reports/antenna_summary.rpt")
    trim = json.load(open(glob.glob(run + "/*-odb-diodetrim/diode_trim.json")[0]))
    return dict(
        tag=run.split("/")[-1], final_antenna_step=last.split("/")[-1],
        antenna=m.get("route__antenna_violation__count"), antenna_rows=len(ant),
        fanout=mx("design__max_fanout_violation__count"), slew=mx("design__max_slew_violation__count"),
        cap=mx("design__max_cap_violation__count"), setup_vio=mx("timing__setup_vio__count"),
        hold_vio=mx("timing__hold_vio__count"), worst_setup=round(ws, 3), worst_hold=round(hs, 3),
        magic=m.get("magic__drc_error__count"), klayout=m.get("klayout__drc_error__count"),
        lvs=m.get("design__lvs_error__count"), area=m.get("design__instance__area"),
        core_util=round(m.get("design__instance__utilization", 0), 3), wire_um=m.get("route__wirelength"),
        die=m.get("design__die__area"), power=m.get("power__total"), flow_err=m.get("flow__errors__count"),
        resolved={k: r.get(k) for k in ("FP_CORE_UTIL", "FANOUT_REPAIR_LIMIT", "CLOCK_PERIOD", "MAX_FANOUT_CONSTRAINT",
                                         "MAX_TRANSITION_CONSTRAINT", "IO_DELAY_CONSTRAINT", "GRT_ANTENNA_MARGIN",
                                         "DIODE_TRIM_KEEP", "DIODE_TRIM_MIN_DIODES")},
        trim={k: trim[k] for k in ("keep", "min_diodes", "nets_trimmed", "diodes_removed", "diodes_before", "diodes_after")},
        ant_pins=[(a[0], a[3], a[4]) for a in ant])

if __name__ == "__main__":
    for run in sys.argv[1:]:
        print(json.dumps(summarize(run)))
