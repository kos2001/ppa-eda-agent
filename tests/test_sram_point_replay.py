import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from sram_point_replay import measurements, relocate, run_bounded, sha, verify_point_sources


class ReplayTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "Linux executable fixture")
    def test_cli_retains_unqualified_point_and_identity_with_fake_simulator(self):
        repo = Path(__file__).resolve().parents[1]
        source = repo / "pipeline/designs/sram_wrapper/experiments/evidence-20261004/characterization-algorithm/completed-ss-port0-20261004"
        reference = json.loads((source / "measurement-summary.json").read_text())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = root / "fake-ngspice"
            output = "\n".join(f"{k} = {v}" for k, v in reference["measurement_values_SI"].items())
            fake.write_text(f"#!{sys.executable}\nimport sys\nfrom pathlib import Path\n"
                            "if '-v' in sys.argv: print('Compiled with KLU')\n"
                            f"else: Path('timing.lis').write_text({output!r})\n")
            fake.chmod(0o700)
            (root / "spinit").write_text("* test startup\n")
            (root / "sram.sp").write_text("* test netlist\n")
            model = root / "pdk/sky130A/libs.tech/ngspice"
            model.mkdir(parents=True)
            (model / "sky130.lib.spice").write_text("* test model\n")
            identity = {"pvt": reference["pvt"], "point": {k: reference[k] for k in
                        ("port", "period_ns", "input_slew_10_90_ns", "load_fF")}}
            identity["stimulus_sources"] = {name: {"retained_bytes_sha256": sha(source / name)}
                                           for name in ("delay_stim.sp", "delay_meas.sp")}
            (root / "point.json").write_text(json.dumps(identity))
            result = subprocess.run([sys.executable, str(repo / "pipeline/sram_point_replay.py"),
                "--source-dir", str(source), "--point-manifest", str(root / "point.json"),
                "--simulator", str(fake), "--simulator-sha256", sha(fake),
                "--netlist", str(root / "sram.sp"), "--netlist-sha256", sha(root / "sram.sp"),
                "--model-root", str(root / "pdk"), "--startup-dir", str(root),
                "--output-dir", str(root / "result"), "--timeout", "5"],
                capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            got = json.loads((root / "result/manifest.json").read_text())
            self.assertEqual(got["status"], "measured_unverified")
            self.assertEqual(got["measurements"]["finite_count"], 27)
            self.assertTrue(got["point_checks"]["point_checks_passed"])
            self.assertFalse(got["model_qualified"])
            self.assertEqual(got["point_identity"], identity)

    def test_changed_electrical_deck_cannot_reuse_prepared_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("delay_stim.sp", "delay_meas.sp"):
                (root / name).write_text("original\n")
            identity = {"sources_sha256": {str(root / name): sha(root / name)
                        for name in ("delay_stim.sp", "delay_meas.sp")}}
            verify_point_sources(root, identity)
            (root / "delay_stim.sp").write_text("changed pulse\n")
            with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                verify_point_sources(root, identity)

    def test_missing_nonfinite_and_duplicate_results_are_unverified(self):
        deck = ".meas tran delay TRIG v(x)\n.meas tran voltage FIND v(y)\n"
        good = measurements(deck, "delay = 1.2e-9 targ=3\nvoltage = 1.6\n")
        self.assertTrue(good["all_requested_finite"])
        for output in ("delay = nan\nvoltage = 1.6", "delay = 1e-9", "delay = 1\ndelay = 2\nvoltage = 1.6"):
            self.assertFalse(measurements(deck, output)["all_requested_finite"])
        with self.assertRaises(ValueError):
            measurements(deck + deck, "")

    def test_relocates_only_includes_preserving_electrical_cards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("sky130.lib.spice", "sram.sp", "delay_meas.sp"):
                (root / name).write_text("* preserved input\n")
            text = '.lib "/old/sky130.lib.spice" ss\n.include "/old/sram.sp"\n.include /old/delay_meas.sp\n.TEMP 100\n.TRAN 10p 150n 0n 10p UIC\n.OPTIONS KLU RELTOL=0.001 method=gear ACCT\n'
            got, changes = relocate(text, root / "sky130.lib.spice", root / "sram.sp", root / "delay_meas.sp")
            self.assertEqual(len(changes), 3)
            self.assertEqual(got.split(".TEMP")[1], text.split(".TEMP")[1])
            self.assertIn('" ss\n', got)
            with self.assertRaises(ValueError):
                relocate(text.replace("/old/sram.sp", "/old/unknown.sp"), root / "sky130.lib.spice", root / "sram.sp", root / "delay_meas.sp")

    @unittest.skipUnless(os.name == "posix", "Linux process groups")
    def test_live_samples_are_readable_before_process_termination(self):
        with tempfile.TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=1) as executor:
            root = Path(tmp)
            command = [sys.executable, "-c",
                       "from pathlib import Path; import time\n"
                       "while not Path('release').exists(): time.sleep(.01)\n"]
            future = executor.submit(run_bounded, command, root, 5)
            live = root / "process_samples.jsonl"
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    if live.exists() and live.read_text(encoding="utf-8").endswith("\n"):
                        break
                    time.sleep(.01)
                samples = [json.loads(line) for line in live.read_text(encoding="utf-8").splitlines()]
                self.assertTrue(samples)
                self.assertFalse(future.done())
                self.assertFalse((root / "process_samples.json").exists())
                os.kill(samples[0]["pid"], 0)
            finally:
                (root / "release").touch()
            result = future.result(timeout=5)
            self.assertEqual(result["returncode"], 0)
            self.assertFalse(result["timed_out"])
            self.assertEqual(json.loads((root / "process_samples.json").read_text()),
                             [json.loads(line) for line in live.read_text(encoding="utf-8").splitlines()])

    @unittest.skipUnless(os.name == "posix", "Linux process groups")
    def test_deadline_terminates_owned_process_and_preserves_partial_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_bounded([sys.executable, "-u", "-c",
                                  "import time; print('partial measurement'); time.sleep(30)"], root, .2)
            self.assertTrue(result["timed_out"])
            self.assertLess(result["wall_seconds"], 5)
            self.assertNotEqual(result["returncode"], 0)
            self.assertIn("partial measurement", (root / "stdout.log").read_text())
            self.assertTrue(json.loads((root / "process_samples.json").read_text()))
            with self.assertRaises(ProcessLookupError):
                os.kill(result["pid"], 0)

    @unittest.skipUnless(os.name == "posix", "Linux process groups")
    def test_normal_exit_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_bounded([sys.executable, "-c", "print('done')"], Path(tmp), 5)
            self.assertFalse(result["timed_out"])
            self.assertEqual(result["returncode"], 0)
