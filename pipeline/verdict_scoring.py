"""Signoff verdict scoring: turns one run's metrics.json into a pass/fail verdict.

Pure functions over a metrics mapping and run_spec targets; no tool is invoked
and no file is read. Split out of orchestrator.py, which re-exports these names.
"""

# The signoff checks a verdict is built from, paired with the label used
# both when the count is nonzero ("3 KLayout DRC error(s)") and when the
# metric is absent entirely ("KLayout DRC error(s) — never checked").
#
# Every entry is a metric OpenLane's own library marks critical=True, so
# the verdict agrees with the tool it trusts rather than a hand-picked
# list. Deliberately excludes lint *warnings* and clock skew: real
# signals, but not pass/fail ones, and promoting a warning to a failure
# would be overreach.
SIGNOFF_METRICS = (
    ("magic__drc_error__count", "Magic DRC error(s)"),
    ("klayout__drc_error__count", "KLayout DRC error(s)"),
    ("design__lvs_error__count", "LVS error(s)"),
    ("design__instance_unmapped__count", "unmapped instance(s) after synthesis"),
    ("design__xor_difference__count", "XOR difference(s) between tool GDS outputs"),
    ("magic__illegal_overlap__count", "illegal layout overlap(s) (Magic)"),
    ("route__drc_errors", "routing DRC error(s)"),
    ("design__lvs_device_difference__count", "LVS device difference(s)"),
    ("design__lvs_net_difference__count", "LVS net difference(s)"),
    ("design__lvs_property_fail__count", "LVS property failure(s)"),
    ("design__lvs_unmatched_device__count", "LVS unmatched device(s)"),
    ("design__lvs_unmatched_net__count", "LVS unmatched net(s)"),
    ("design__lvs_unmatched_pin__count", "LVS unmatched pin(s)"),
    ("design__disconnected_pin__count", "disconnected pin(s)"),
    ("timing__setup_vio__count", "setup timing violation(s)"),
    ("timing__hold_vio__count", "hold timing violation(s)"),
    ("route__antenna_violation__count", "routing antenna violation(s)"),
    ("design__power_grid_violation__count", "power-grid violation(s)"),
    ("design__max_slew_violation__count", "max-slew (DRV) violation(s)"),
    ("design__max_cap_violation__count", "max-capacitance (DRV) violation(s)"),
    ("design__max_fanout_violation__count", "max-fanout (DRV) violation(s)"),
    ("synthesis__check_error__count", "synthesis check error(s)"),
    ("design__lint_error__count", "RTL lint error(s)"),
)


def supply_rails(metrics: dict) -> list[dict]:
    """Per-supply-net IR drop, from OpenLane's own power-grid analysis.

    Reads `design_powergrid__drop__worst__net:<net>` and the matching
    `..._voltage__worst__net:<net>`, deliberately ignoring the
    `drop__average__net:` keys — for VPWR that key holds 1.79999 on a
    1.8 V rail, i.e. a voltage rather than a drop, and building a gate on
    a metric whose meaning has to be guessed is how fabricated numbers
    get into a verdict.

    Nominal is derived from the pair rather than assumed: worst voltage
    plus worst drop is the rail's nominal (1.79991 + 0.0000902 = 1.8 on a
    real run). Ground nets sit at 0 V nominal, where a percentage is
    meaningless, so drop_pct is left None and the absolute bounce is
    still reported.
    """
    prefix = "design_powergrid__drop__worst__net:"
    rails = []
    for key in sorted(metrics):
        if not key.startswith(prefix) or "__corner:" in key:
            continue
        net = key[len(prefix):]
        drop = metrics[key]
        volt = metrics.get(f"design_powergrid__voltage__worst__net:{net}")
        nominal = None
        pct = None
        if isinstance(drop, (int, float)) and isinstance(volt, (int, float)):
            nominal = volt + drop
            # A ground rail reports its bounce as both drop and voltage,
            # so nominal comes out at twice the bounce — near zero, not a
            # supply. Percentages against it would be nonsense.
            if nominal > 0.1:
                pct = 100.0 * drop / nominal
        rails.append({
            "net": net,
            "drop_worst_v": drop,
            "voltage_worst_v": volt,
            "nominal_v": nominal,
            "drop_pct": pct,
        })
    return rails


def worst_setup_slack(metrics: dict) -> float | None:
    """Worst setup slack over every analysed corner, in ns (positive is
    margin). None when the run reported none — never a default."""
    corner = [v for k, v in metrics.items()
              if k.startswith("timing__setup__ws__corner:")
              and isinstance(v, (int, float))]
    if corner:
        return min(corner)
    value = metrics.get("timing__setup__ws")
    return value if isinstance(value, (int, float)) else None


def score(metrics: dict, targets: dict) -> dict:
    """Checks a real metrics.json against run_spec targets.

    Returns {"passed": bool, "violations": [...], "area": float}.
    Every field read here is a real OpenLane metric key — see
    docs/superpowers/specs/2026-08-21-autonomous-layout-agent-design.md
    for why we trust metrics.json rather than re-deriving PPA ourselves.
    """
    violations = []
    unverified = []

    # Signoff gates OpenLane computes and this verdict was ignoring.
    #
    # An audit of a real completed run found OpenLane emitting 279
    # metrics of which score() read 32 — and among the 247 discarded were
    # these, every one a genuine pass/fail signal the pipeline claims to
    # care about. The most consequential is klayout__drc_error__count: a
    # SECOND, independent DRC signoff. Only Magic's was checked, so a
    # candidate that KLayout flagged and Magic did not would have been
    # reported PASS.
    #
    # Antenna violations are a real manufacturing failure, not a warning.
    # The max_slew/max_cap/max_fanout counts are the same DRV family that
    # produces RSZ-0090 — the failure mode this project has spent the most
    # effort diagnosing — and they were sitting in metrics.json as
    # structured numbers the whole time.
    #
    # Only hard, unambiguous failure counts are gated here. Lint
    # *warnings* and clock skew are deliberately not: they are real
    # signals but not pass/fail ones, and turning a warning into a
    # failure would be overreach.
    # OpenLane's own metric library marks a specific set of metrics
    # `critical=True` — its own declaration of what constitutes a fatal
    # result. Gating on that list rather than a hand-picked one makes the
    # verdict agree with the tool it trusts, instead of guessing which
    # failures matter. Extracted from
    # openlane/common/metrics/library.py in the pinned image.
    # A missing metric used to read as a pass.
    #
    # The loop below was `count = metrics.get(key); if count:` — so a
    # check that never ran scored identically to a check that ran clean.
    # That is reachable, not hypothetical: OpenLane 2 skips steps via the
    # flow CLI (`--skip`, `--to`), and this project has already done it
    # deliberately (`--skip OpenROAD.RepairAntennas` while chasing
    # sram_wrapper). Demonstrated directly by stopping a real run at
    # OpenROAD.STAPostPNR, one step before the DRC/LVS/XOR block: none of
    # those metrics exist, and the old code called it PASS.
    #
    # A completed run really does emit all of these — audited against a
    # full counter4_tinydie signoff, which produced 281 metrics including
    # every key below with value 0. So absence means the step did not
    # run, and requiring presence cannot false-alarm on a good run.
    #
    # Absence is tracked separately from a nonzero count rather than
    # folded into violations. "Found 3 DRC errors" and "never checked
    # DRC" both block a pass, but they are different facts and a reader
    # needs to tell them apart — the same distinction this pipeline draws
    # between a measured limit and an assumed one.
    # The same facts, one row per check, so a reader can see all 23 at
    # once — clean, violated, or never run — instead of reconstructing
    # the clean ones as "whatever is in neither list". count is None
    # when the check never ran; that is the only way None appears.
    signoff_checks = []
    for key, label in SIGNOFF_METRICS:
        count = metrics.get(key)
        if count is None:
            unverified.append(label)
        elif count:
            violations.append(f"{count} {label}")
        signoff_checks.append({"key": key, "label": label, "count": count})

    max_util = targets.get("max_core_utilization")
    util = metrics.get("design__instance__utilization__stdcell")
    if max_util is not None and util is not None and util > max_util:
        violations.append(f"utilization {util:.3f} > target {max_util}")

    # Worst setup slack across corners; OpenLane emits one WNS key per
    # corner (timing__setup__wns__corner:<name>) — a negative value on
    # any of them is a real timing violation at that corner.
    setup_wns_keys = [k for k in metrics if k.startswith("timing__setup__wns__corner:")]
    # A source with no corner breakdown (iEDA reports one liberty set,
    # per clock — see ieda_metrics.py) carries only the design-level
    # key, which OpenLane also emits. Read it when the corners are
    # absent; never let it override them when they are present.
    if setup_wns_keys:
        worst_wns = min(metrics[k] for k in setup_wns_keys)
    else:
        worst_wns = metrics.get("timing__setup__wns", 0)
    if worst_wns < 0:
        violations.append(f"worst setup WNS {worst_wns} (timing violation)")

    # Hold. This was recorded per corner and displayed on the dashboard
    # but never gated on, so a candidate with a real hold violation was
    # reported PASS while showing the negative slack on screen —
    # demonstrated directly with hold_wns -0.25 and 7 hold violations
    # scoring as a pass. Hold violations are silicon-fatal and cannot be
    # fixed after fabrication, which makes this the worst thing the
    # verdict could have been silent about.
    hold_wns_keys = [k for k in metrics if k.startswith("timing__hold__wns__corner:")]
    if hold_wns_keys:
        worst_hold = min(metrics[k] for k in hold_wns_keys)
    else:
        worst_hold = metrics.get("timing__hold__wns", 0)
    if worst_hold < 0:
        violations.append(f"worst hold WNS {worst_hold} (hold violation)")

    # Every real timing corner OpenLane actually analyzed (typically 9:
    # {min,nom,max} x {ff_n40C_1v95, tt_025C_1v80, ss_100C_1v60}), setup
    # and hold WNS for each — not just the single worst value, so the
    # dashboard can show real per-PVT-corner timing instead of one number.
    timing_corners = []
    for key in setup_wns_keys:
        corner = key[len("timing__setup__wns__corner:"):]
        hold_key = f"timing__hold__wns__corner:{corner}"
        timing_corners.append({
            "corner": corner,
            "setup_wns": metrics[key],
            "hold_wns": metrics.get(hold_key),
        })
    timing_corners.sort(key=lambda c: c["corner"])

    # Real power, still OpenLane's own default/vectorless estimate:
    # score() reads metrics.json, and OpenSTA computed these numbers from
    # a default toggle rate rather than from a workload. Real computed
    # values, not fabricated — but an estimate.
    #
    # The activity-annotated measurement now lives beside it, under the
    # verdict's `power_activity` key, put there by run_candidate()
    # because it needs the design and the run directory that score()
    # never sees. The two are not interchangeable: on spm the same
    # netlist reads 1.33e-03 W here against 1.53e-03 W measured, with
    # combinational power understated by 44%. Anything comparing
    # candidates must pick one basis for all of them — see pick_winner().
    #
    # Followed by real IR-drop/power-grid numbers from the actual PDN
    # OpenROAD generated.
    power = None
    if "power__total" in metrics:
        power = {
            "internal_w": metrics.get("power__internal__total"),
            "leakage_w": metrics.get("power__leakage__total"),
            "switching_w": metrics.get("power__switching__total"),
            "total_w": metrics.get("power__total"),
        }
    power_domain = None
    if "ir__voltage__worst" in metrics:
        power_domain = {
            "ir_drop_avg_v": metrics.get("ir__drop__avg"),
            "ir_drop_worst_v": metrics.get("ir__drop__worst"),
            "voltage_worst_v": metrics.get("ir__voltage__worst"),
            # Per supply net, which is the actual power-domain view and
            # was being thrown away: OpenLane emits
            # design_powergrid__drop__worst__net:<net> for each net it
            # analysed (VPWR, VGND, and a macro's own vccd1/vssd1 once it
            # is hooked into the grid), and score() collapsed all of them
            # into one global worst number. A design whose macro domain
            # droops badly while the core domain is fine looked identical
            # to one where everything was fine.
            "supplies": supply_rails(metrics),
        }
    # IR drop is a real signoff criterion — enough droop and the cells
    # miss the timing the corner libraries promise — but what counts as
    # too much is a design decision, not a universal constant. So it is
    # gated only when the spec says so, rather than against a number this
    # pipeline invented.
    max_ir_pct = targets.get("max_ir_drop_pct")
    if max_ir_pct is not None:
        for rail in (power_domain or {}).get("supplies", []):
            if rail["drop_pct"] is not None and rail["drop_pct"] > max_ir_pct:
                violations.append(
                    f"IR drop {rail['drop_pct']:.2f}% on {rail['net']} "
                    f"> target {max_ir_pct}%"
                )

    return {
        # An unverified check blocks a pass as firmly as a failed one:
        # "we did not look" is not evidence of clean silicon. Kept as a
        # separate field so the console can say which it was.
        "passed": not violations and not unverified,
        "violations": violations,
        "unverified": unverified,
        "signoff_checks": signoff_checks,
        # Which tool's numbers these are. OpenLane's metrics.json has no
        # such key; a second source (ieda_metrics.py) sets it.
        "metrics_source": metrics.get("metrics__source", "OpenLane metrics.json"),
        "area_um2": metrics.get("design__instance__area"),
        "utilization": util,
        "worst_setup_wns": worst_wns,
        # What placement and routing actually produced, recorded beside
        # the pass/fail. worst_setup_wns above is OpenSTA's *negative*
        # slack, clipped at 0, so it is 0 for every passing candidate and
        # says nothing about margin; the slack itself lives in
        # timing__setup__ws. The core area is the silicon the floorplan
        # costs, which instance area cannot see: 290 um^2 of cells in a
        # 631 um^2 core and in a 480 um^2 core are the same "area" here.
        "worst_setup_slack": worst_setup_slack(metrics),
        "core_area_um2": metrics.get("design__core__area"),
        "wirelength_um": metrics.get("route__wirelength"),
        "via_count": metrics.get("route__vias"),
        "timing_corners": timing_corners,
        "power": power,
        "power_domain": power_domain,
    }
