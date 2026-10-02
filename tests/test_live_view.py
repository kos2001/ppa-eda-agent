"""Tests for the live run observer: log parsers, snapshots, rendering, events.

The log excerpts are verbatim from a real OpenLane 2.3.10 run of gcd on
sky130hd; the parsers read OpenROAD's own wording, so testing them on a
paraphrase would only prove they handle the paraphrase.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline"))

import live_events  # noqa: E402
import live_parse as lp  # noqa: E402
import live_render  # noqa: E402
import live_view  # noqa: E402

GPL = """\
+ global_placement -density 0.500753 -timing_driven -routability_driven -pad_right 0
[NesterovSolve] Iter:    1 overflow: 0.644 HPWL: 3984846
[INFO GPL-0100] Timing-driven: executing resizer for reweighting nets.
[INFO GPL-0103] Timing-driven: weighted 24 nets.
[NesterovSolve] Iter:   10 overflow: 0.462 HPWL: 3936671
[NesterovSolve] Iter:  330 overflow: 0.140 HPWL: 4028775
[NesterovSolve] Iter:  340 overflow: 0.117 HPWL: 4049398
"""
RSZ_HOLD = """\
[INFO RSZ-0098] No setup violations found
[INFO RSZ-0046] Found 21 endpoints with hold violations.
Iteration | Resized | Buffers | Cloned Gates |   WNS   |   TNS   | Endpoint
---------------------------------------------------------------------------
        0 |       0 |       0 |            0 |   0.046 |   0.000 | _358_/D
    final |       0 |      27 |            0 |   0.103 |   0.000 | _389_/D
[INFO RSZ-0032] Inserted 27 hold buffers.
"""
CTS = """\
[INFO CTS-0010]  Clock net "clk" has 35 sinks.
[INFO CTS-0018]     Created 5 clock buffers.
[INFO CTS-0017]     Max level of the clock tree: 2.
"""
GRT = """\
Layer         Resource        Demand        Usage (%)    Max H / Max V / Total Overflow
met1              1884           441           23.41%             0 /  0 /  0
Total             5931           909           15.33%             0 /  0 /  0
[INFO GRT-0018] Total wirelength: 10964 um
"""
DRT_DONE = """\
[INFO DRT-0195] Start 0th optimization iteration.
    Completing 60% with 51 violations.
[INFO DRT-0199]   Number of violations = 73.
[INFO DRT-0195] Start 1st optimization iteration.
[INFO DRT-0199]   Number of violations = 28.
[INFO DRT-0195] Start 2nd optimization iteration.
[INFO DRT-0199]   Number of violations = 41.
[INFO DRT-0195] Start 3rd optimization iteration.
[INFO DRT-0199]   Number of violations = 0.
"""
DRT_LIVE = """\
[INFO DRT-0195] Start 0th optimization iteration.
[INFO DRT-0199]   Number of violations = 73.
[INFO DRT-0195] Start 1st optimization iteration.
    Completing 30% with 13 violations.
"""
DEF = """\
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 100000 100000 ) ;
COMPONENTS 4 ;
    - PHY_EDGE sky130_fd_sc_hd__decap_3 + SOURCE DIST + FIXED ( 5000 5000 ) N ;
    - _1_ sky130_fd_sc_hd__nand2_1 + PLACED ( 10000 10000 ) N ;
    - _2_ sky130_fd_sc_hd__nand2_1 + PLACED ( 12000 11000 ) N ;
    - _3_ sky130_fd_sc_hd__dfxtp_1 + PLACED ( 90000 90000 ) N ;
END COMPONENTS
"""


class TestParsers(unittest.TestCase):
    def test_global_placement_converging(self):
        g = lp.parse_gpl(GPL)
        self.assertEqual((g["iter"], g["overflow"], g["hpwl"]), (340, 0.117, 4049398))
        self.assertEqual(len(g["series"]), 4)
        self.assertEqual(g["timing_reweights"], 1)
        self.assertIsNone(g["finished"])

    def test_global_placement_finished(self):
        g = lp.parse_gpl(GPL + "[NesterovSolve] Finished with Overflow: 0.099522\n")
        self.assertAlmostEqual(g["finished"], 0.099522)

    def test_resizer_last_row_and_hold_counts(self):
        r = lp.parse_resizer(RSZ_HOLD)
        self.assertEqual(r["row"]["Iteration"], "final")
        self.assertEqual(r["row"]["Buffers"], "27")
        self.assertEqual((r["hold_endpoints"], r["hold_buffers"]), (21, 27))
        self.assertTrue(r["setup_clean"])
        self.assertNotIn("buffer_cap_hit", r)

    def test_resizer_buffer_cap_is_flagged(self):
        self.assertTrue(lp.parse_resizer(RSZ_HOLD + "[ERROR RSZ-0060] Max buffer count reached.\n")
                        ["buffer_cap_hit"])

    def test_cts(self):
        self.assertEqual(lp.parse_cts(CTS), {"sinks": 35, "buffers": 5, "levels": 2})

    def test_global_routing(self):
        g = lp.parse_grt(GRT)
        self.assertEqual((g["usage_pct"], g["overflow"], g["wirelength_um"]), (15.33, 0, 10964))

    def test_detailed_routing_history_is_the_convergence_curve(self):
        d = lp.parse_drt(DRT_DONE)
        self.assertEqual(d["history"], [73, 28, 41, 0])
        self.assertEqual(d["violations"], 0)

    def test_detailed_routing_midway_shows_the_live_count(self):
        d = lp.parse_drt(DRT_LIVE)
        self.assertEqual((d["history"], d["iteration"], d["pct"], d["violations"]),
                         ([73], 1, 30, 13))

    def test_unrecognised_text_degrades_to_nothing_not_an_error(self):
        for fn in (lp.parse_gpl, lp.parse_resizer, lp.parse_cts, lp.parse_grt, lp.parse_drt):
            fn("the log of some other tool\n")  # must not raise

    def test_step_slug_dispatch(self):
        self.assertEqual(lp.parser_for("openroad-globalplacement")[0], "GPL")
        self.assertIsNone(lp.parser_for("openroad-globalplacementskipio"))
        self.assertEqual(lp.parser_for("openroad-resizertimingpostcts")[0], "RSZ")
        self.assertEqual(lp.parser_for("openroad-detailedrouting")[0], "DRT")
        self.assertIsNone(lp.parser_for("magic-drc"))


class TestDrawing(unittest.TestCase):
    def test_density_grid_counts_movable_cells_only(self):
        info = lp.density_grid(DEF, 4, 4)
        self.assertEqual(info["cells"], 3)  # the FIXED decap is not a cell to watch
        self.assertEqual(sum(map(sum, info["grid"])), 3)
        self.assertEqual(info["grid"][3][0], 2)   # low-left, y flipped for the screen
        self.assertEqual(info["grid"][0][3], 1)   # top-right

    def test_floorplan_with_no_placed_cells_has_no_map(self):
        self.assertIsNone(lp.density_grid(DEF.replace("PLACED", "UNPLACED"), 4, 4))

    def test_sparkline_scales_to_its_own_range_and_survives_a_flat_series(self):
        self.assertEqual(lp.sparkline([1, 2, 3], ascii_only=True)[0], "_")
        self.assertEqual(len(set(lp.sparkline([5, 5, 5]))), 1)
        self.assertEqual(lp.sparkline([]), "")

    def test_bar_is_clamped(self):
        self.assertEqual(lp.bar(2.0, 4, True), "####")
        self.assertEqual(lp.bar(-1.0, 4, True), "----")


def make_run(root: Path, steps: list[tuple[str, str, str]], done_last=False, final=False,
             error="") -> Path:
    run = root / "runs" / "t1"
    run.mkdir(parents=True)
    (run / "resolved.json").write_text("{}")
    (run / "error.log").write_text(error)
    for i, (slug, log, _) in enumerate(steps, start=1):
        d = run / f"{i:02d}-{slug}"
        d.mkdir()
        (d / f"{slug}.log").write_text(log)
        if i < len(steps) or done_last:
            (d / "state_out.json").write_text("{}")
    if final:
        (run / "final").mkdir()
        (run / "final" / "metrics.json").write_text(json.dumps({
            "design__instance__area": 3004.0, "power__total": 7.4e-4,
            "timing__setup__ws__corner:a": 3.0, "timing__setup__ws__corner:b": 1.8}))
    return run


class TestSnapshot(unittest.TestCase):
    def test_a_run_in_global_placement_reports_its_convergence(self):
        with tempfile.TemporaryDirectory() as t:
            run = make_run(Path(t), [("openroad-floorplan", "", ""),
                                     ("openroad-globalplacement", GPL, "")])
            s = live_view.run_snapshot(run, time.time())
            self.assertEqual((s["status"], s["step_no"], s["label"]),
                             ("running", 2, "Global placement"))
            self.assertEqual(s["telemetry"]["kind"], "GPL")
            self.assertEqual(s["telemetry"]["iter"], 340)

    def test_a_completed_run_reports_its_final_numbers(self):
        with tempfile.TemporaryDirectory() as t:
            run = make_run(Path(t), [("openroad-floorplan", "", "")], done_last=True, final=True)
            s = live_view.run_snapshot(run, time.time())
            self.assertEqual(s["status"], "done")
            self.assertEqual(s["finished"]["area"], 3004.0)
            self.assertEqual(s["finished"]["setup_ws"], 1.8)  # worst corner

    def test_an_error_log_on_a_run_that_went_quiet_means_failed(self):
        """Host cannot say whether the process is alive: a long silence decides."""
        with tempfile.TemporaryDirectory() as t:
            run = make_run(Path(t), [("openroad-cts", CTS, "")], error="[RSZ-0060] Max buffer count reached.\n")
            saved = live_view.process_alive
            live_view.process_alive = lambda r: None
            try:
                s = live_view.run_snapshot(run, time.time() + live_view.FAIL_IDLE_S + 5)
            finally:
                live_view.process_alive = saved
            self.assertEqual(s["status"], "failed")
            self.assertIn("RSZ-0060", s["error"])

    def test_a_live_process_is_never_called_failed_however_quiet(self):
        """A KLayout DRC step under load average 36 went quiet for longer than
        the old 20 s threshold and a healthy run was painted failed - twice."""
        with tempfile.TemporaryDirectory() as t:
            run = make_run(Path(t), [("klayout-drc", "", "")], error='Error while reading cell "x"\n')
            saved = live_view.process_alive
            live_view.process_alive = lambda r: True
            try:
                s = live_view.run_snapshot(run, time.time() + 3000)
            finally:
                live_view.process_alive = saved
            self.assertNotEqual(s["status"], "failed")

    def test_a_dead_process_with_an_error_log_is_failed_at_once(self):
        with tempfile.TemporaryDirectory() as t:
            run = make_run(Path(t), [("openroad-cts", CTS, "")], error="[RSZ-0060] Max buffer count reached.\n")
            saved = live_view.process_alive
            live_view.process_alive = lambda r: False
            try:
                s = live_view.run_snapshot(run, time.time())  # no idle at all
            finally:
                live_view.process_alive = saved
            self.assertEqual(s["status"], "failed")

    def test_an_error_log_on_a_run_still_moving_is_not_failure(self):
        """A successful sram_wrapper run leaves 5 KB of KLayout read errors in
        error.log and carries on to signoff. Painting it failed on step 69
        was a real false alarm."""
        with tempfile.TemporaryDirectory() as t:
            run = make_run(Path(t), [("openroad-cts", CTS, "")],
                           error='Error while reading cell "x": Unknown layer/datatype\n')
            saved = live_view.process_alive
            live_view.process_alive = lambda r: None
            try:
                s = live_view.run_snapshot(run, time.time())
            finally:
                live_view.process_alive = saved
            self.assertEqual(s["status"], "running")
            self.assertGreater(s["tool_errors"], 0)

    def test_process_alive_is_none_or_a_bool_for_a_directory_nothing_runs(self):
        with tempfile.TemporaryDirectory() as t:
            run = Path(t) / "runs" / "nothing-here"
            run.mkdir(parents=True)
            self.assertIn(live_view.process_alive(run), (None, False))

    def test_a_finished_run_with_an_error_log_is_done(self):
        with tempfile.TemporaryDirectory() as t:
            run = make_run(Path(t), [("openroad-cts", "", "")], done_last=True, final=True,
                           error="Error while reading cell\n")
            s = live_view.run_snapshot(run, time.time() + 3600)
            self.assertEqual(s["status"], "done")

    def test_a_quiet_run_is_stalled_not_running(self):
        with tempfile.TemporaryDirectory() as t:
            run = make_run(Path(t), [("openroad-cts", CTS, "")])
            s = live_view.run_snapshot(run, time.time() + 3600)
            self.assertEqual(s["status"], "stalled")

    def test_an_empty_run_directory_is_starting_not_an_error(self):
        with tempfile.TemporaryDirectory() as t:
            run = Path(t) / "runs" / "t1"
            run.mkdir(parents=True)
            self.assertEqual(live_view.run_snapshot(run, time.time())["step_no"], 0)

    def test_old_runs_are_hidden_unless_asked_for(self):
        with tempfile.TemporaryDirectory() as t:
            design = Path(t)
            make_run(design, [("openroad-cts", CTS, "")])
            self.assertEqual(len(live_view.discover([design], time.time(), 60, False)), 1)
            self.assertEqual(len(live_view.discover([design], time.time() + 9999, 60, False)), 0)
            self.assertEqual(len(live_view.discover([design], time.time() + 9999, 60, True)), 1)


class TestRender(unittest.TestCase):
    def data(self, tmp):
        run = make_run(Path(tmp), [("openroad-detailedrouting", DRT_LIVE, "")])
        snap = live_view.run_snapshot(run, time.time())
        snap["design"] = "d"
        return {"now": time.time(), "runs": [snap], "events": {"d": []}}

    def test_a_running_flow_shows_its_algorithm_state(self):
        with tempfile.TemporaryDirectory() as t:
            text = "\n".join(live_render.render(self.data(t), 100, False, True))
            self.assertIn("Detailed routing", text)
            self.assertIn("violations 13", text)
            self.assertIn("history 73", text)

    def test_ascii_mode_has_no_wide_glyphs(self):
        with tempfile.TemporaryDirectory() as t:
            text = "\n".join(live_render.render(self.data(t), 100, False, True))
            text.encode("ascii")

    def test_decisions_are_listed_with_their_reasons(self):
        evs = [{"t": 100.0, "type": "run_start", "max_iterations": 3},
               {"t": 130.0, "type": "repair", "from_tag": "a", "to_tag": "a-iter1",
                "code": "RSZ-0060", "changes": {"PL_RESIZER_HOLD_SLACK_MARGIN": [None, 0.0]}},
               {"t": 190.0, "type": "polish_trial", "move": "hold-margin-0", "adopted": True,
                "deltas": {"area": -9.0, "power": -3.2, "margin": 0.9}}]
        data = {"now": 200.0, "runs": [], "events": {"d": evs}}
        text = "\n".join(live_render.render(data, 120, False, True))
        self.assertIn("REPAIR a -> a-iter1  [RSZ-0060]", text)
        self.assertIn("ADOPTED", text)
        self.assertIn("area -9.0%", text)

    def test_nothing_running_says_so(self):
        text = "\n".join(live_render.render({"now": 0, "runs": [], "events": {}}, 80, False, True))
        self.assertIn("no recent runs", text)


class TestEvents(unittest.TestCase):
    def test_events_round_trip_and_only_the_latest_run_is_returned(self):
        with tempfile.TemporaryDirectory() as t:
            live_events.emit(t, "run_start", n=1)
            live_events.emit(t, "stop", reason="x")
            live_events.emit(t, "run_start", n=2)
            live_events.emit(t, "iteration_start", iteration=1)
            got = live_events.read(t)
            self.assertEqual([e["type"] for e in got], ["run_start", "iteration_start"])
            self.assertEqual(got[0]["n"], 2)

    def test_a_half_written_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            live_events.emit(t, "run_start")
            with open(live_events.events_path(t), "a", encoding="utf-8") as f:
                f.write('{"type": "cand')
            self.assertEqual(len(live_events.read(t)), 1)

    def test_an_unwritable_location_never_raises(self):
        """A path whose parent is a regular file cannot be created on any
        platform (a fixed absolute path would be created on Windows)."""
        with tempfile.NamedTemporaryFile() as f:
            bad = os.path.join(f.name, "design")
            live_events.emit(bad, "run_start")
            self.assertEqual(live_events.read(bad), [])

    def test_the_events_file_is_in_the_design_directory_not_runs(self):
        """runs/ is created by the container as root; a host-side write
        there silently fails (found on the WSL runner)."""
        self.assertEqual(live_events.events_path("/d").parent, Path("/d"))


if __name__ == "__main__":
    unittest.main()
