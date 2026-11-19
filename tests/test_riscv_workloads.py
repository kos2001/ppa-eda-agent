"""Host ISA model and workload generator for riscv32i throughput evidence."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import riscv_workloads as rw


def s32(v):
    return v - (1 << 32) if v >> 31 else v


class EncodingTests(unittest.TestCase):
    def test_known_encodings(self):
        # Reference values from the RISC-V spec / GNU as.
        words, _ = rw.assemble([
            ("addi", "x1", "x0", 1), ("sw", "x3", 0, "x2"), ("lw", "x6", 4, "x5"),
            ("add", "x3", "x3", "x6"), ("sub", "x3", "x3", "x7"), ("lui", "x1", 1),
            "self", ("jal", "x0", "self")])
        self.assertEqual(words, [0x00100093, 0x00312023, 0x0042A303, 0x006181B3,
                                 0x407181B3, 0x000010B7, 0x0000006F])

    def test_backward_branch_offset(self):
        words, _ = rw.assemble(["top", ("addi", "x4", "x4", -1), ("bne", "x4", "x0", "top")])
        self.assertEqual(words[1], 0xFE021EE3)  # bne x4, x0, -4

    def test_out_of_range_immediate_is_rejected(self):
        with self.assertRaises(ValueError):
            rw.assemble([("addi", "x1", "x0", 4096)])


class WorkloadResultTests(unittest.TestCase):
    """The ISS output must equal an independent Python computation."""

    def outputs(self, name, seed=17):
        b = rw.build(name, seed)
        data = rw.input_words(name, seed, rw.WORKLOADS[name][1])
        return data, [v for _, v in b["checks"]], b

    def test_sum(self):
        data, out, _ = self.outputs("sum64")
        self.assertEqual(out, [sum(data) & rw.MASK])

    def test_fib(self):
        data, out, _ = self.outputs("fib40")
        a, b, exp = data[0], 1, []
        for _ in range(40):
            exp.append(a & rw.MASK)
            a, b = b, (a + b) & rw.MASK
        self.assertEqual(out, exp)

    def test_sort(self):
        data, out, _ = self.outputs("bubble24")
        self.assertEqual([s32(v) for v in out], sorted(s32(v) for v in data))

    def test_mul(self):
        data, out, _ = self.outputs("mul16")
        self.assertEqual(out, [(data[2 * i] * data[2 * i + 1]) & rw.MASK for i in range(16)])

    def test_popcount(self):
        data, out, _ = self.outputs("popcount32")
        counts = [bin(v).count("1") for v in data]
        self.assertEqual(out, counts + [sum(counts)])

    def test_checksum(self):
        data, out, _ = self.outputs("checksum96")
        h, neg = 0, 0
        for v in data:
            h = ((h << 5) | (h >> 27)) & rw.MASK
            h ^= v
            n = int(s32(v) < 0)
            neg += n
            h = (h - n) & rw.MASK
        self.assertEqual(out, [h, neg])

    def test_heldout_and_visible_partition_the_workloads(self):
        self.assertEqual(set(rw.VISIBLE) | set(rw.HELD_OUT), set(rw.WORKLOADS))
        self.assertFalse(set(rw.VISIBLE) & set(rw.HELD_OUT))


class GeneratedBenchTests(unittest.TestCase):
    def test_checked_in_testbench_matches_generator(self):
        tb = ROOT / "pipeline" / "designs" / "riscv32i" / "verify" / "riscv_perf_tb.sv"
        self.assertEqual(tb.read_text(encoding="utf-8"), rw.generate_tb(),
                         "regenerate with: python pipeline/riscv_workloads.py "
                         "--out pipeline/designs/riscv32i/verify/riscv_perf_tb.sv")

    def test_bench_prints_perf_for_every_workload_and_seed(self):
        tb = rw.generate_tb()
        for name in rw.WORKLOADS:
            self.assertEqual(tb.count(f"PPA_PERF workload={name} "), len(rw.SEEDS))


if __name__ == "__main__":
    unittest.main()
