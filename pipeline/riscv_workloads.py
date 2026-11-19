#!/usr/bin/env python3
"""RV32I workloads + host-side ISA model for riscv32i throughput measurement.

Gives throughput_score.py real IPC evidence for the riscv32i core.

  * A two-pass assembler for the RV32I subset the workloads use.
  * An ISA simulator written from the RISC-V spec, independent of the RTL.
    It supplies the ARCHITECTURAL instruction count and the expected memory
    signature for each (workload, seed).
  * A generator that writes a self-checking SystemVerilog testbench
    (`verify/riscv_perf_tb.sv`) with programs, data, expected signatures and
    ISS instruction counts embedded.  The RTL simulation measures CYCLES and
    $fatal's on any signature mismatch, then prints
        PPA_PERF workload=<w> instructions=<ISS count> cycles=<RTL cycles>
    so IPC = architectural instructions / measured cycles.

The core is driven directly (DESIGN_NAME `riscv`: instruction and data
memory are testbench models), so the measurement covers exactly the
synthesized block.  Instructions outside this subset are not exercised;
passing these workloads is not an RV32I compliance claim.

Usage:
    riscv_workloads.py --out pipeline/designs/riscv32i/verify/riscv_perf_tb.sv
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

MASK = 0xFFFFFFFF
DATA_BASE = 0x1000
MEM_WORDS = 2048          # testbench data memory (word addressed)
SEEDS = (1, 17, 101)
REG = {f"x{i}": i for i in range(32)}

# ── assembler ──────────────────────────────────────────

R_OPS = {"add": (0, 0), "sub": (0x20, 0), "sll": (0, 1), "slt": (0, 2),
         "xor": (0, 4), "srl": (0, 5), "or": (0, 6), "and": (0, 7)}
I_OPS = {"addi": 0, "slti": 2, "xori": 4, "ori": 6, "andi": 7}
SH_OPS = {"slli": (0, 1), "srli": (0, 5)}
B_OPS = {"beq": 0, "bne": 1, "blt": 4, "bge": 5}


def _imm(v, bits):
    lo, hi = -(1 << (bits - 1)), (1 << (bits - 1)) - 1
    if not lo <= v <= hi:
        raise ValueError(f"immediate {v} out of {bits}-bit range")
    return v & ((1 << bits) - 1)


def encode(ins, pc, labels):
    op, *a = ins
    if op in R_OPS:
        f7, f3 = R_OPS[op]
        rd, rs1, rs2 = (REG[x] for x in a)
        return f7 << 25 | rs2 << 20 | rs1 << 15 | f3 << 12 | rd << 7 | 0x33
    if op in I_OPS:
        rd, rs1, imm = REG[a[0]], REG[a[1]], _imm(a[2], 12)
        return imm << 20 | rs1 << 15 | I_OPS[op] << 12 | rd << 7 | 0x13
    if op in SH_OPS:
        f7, f3 = SH_OPS[op]
        rd, rs1, sh = REG[a[0]], REG[a[1]], a[2]
        assert 0 <= sh < 32
        return f7 << 25 | sh << 20 | rs1 << 15 | f3 << 12 | rd << 7 | 0x13
    if op == "lw":                       # lw rd, off(rs1) -> ("lw", rd, off, rs1)
        rd, imm, rs1 = REG[a[0]], _imm(a[1], 12), REG[a[2]]
        return imm << 20 | rs1 << 15 | 2 << 12 | rd << 7 | 0x03
    if op == "sw":                       # sw rs2, off(rs1)
        rs2, imm, rs1 = REG[a[0]], _imm(a[1], 12), REG[a[2]]
        return ((imm >> 5) << 25 | rs2 << 20 | rs1 << 15 | 2 << 12
                | (imm & 31) << 7 | 0x23)
    if op in B_OPS:
        rs1, rs2 = REG[a[0]], REG[a[1]]
        off = _imm(labels[a[2]] - pc, 13)
        return ((off >> 12 & 1) << 31 | (off >> 5 & 63) << 25 | rs2 << 20
                | rs1 << 15 | B_OPS[op] << 12 | (off >> 1 & 15) << 8
                | (off >> 11 & 1) << 7 | 0x63)
    if op == "lui":
        return (a[1] & 0xFFFFF) << 12 | REG[a[0]] << 7 | 0x37
    if op == "jal":
        rd = REG[a[0]]
        off = _imm(labels[a[1]] - pc, 21)
        return ((off >> 20 & 1) << 31 | (off >> 1 & 0x3FF) << 21
                | (off >> 11 & 1) << 20 | (off >> 12 & 0xFF) << 12 | rd << 7 | 0x6F)
    raise ValueError(f"unsupported instruction {op}")


def assemble(program):
    labels, pc, flat = {}, 0, []
    for item in program:
        if isinstance(item, str):
            labels[item] = pc
        else:
            flat.append((pc, item))
            pc += 4
    return [encode(ins, p, labels) for p, ins in flat], labels


# ── ISA simulator (from the spec, not from the RTL) ────

def _sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def _s32(v):
    return _sx(v, 32)


def iss(words, mem, halt_pc, limit=2_000_000):
    """Run until pc == halt_pc. Returns (retired instructions, mem)."""
    x = [0] * 32
    pc, n = 0, 0
    while pc != halt_pc:
        if n >= limit:
            raise RuntimeError("ISS instruction limit hit")
        w = words[pc >> 2]
        op, rd, f3 = w & 0x7F, w >> 7 & 31, w >> 12 & 7
        rs1, rs2, f7 = w >> 15 & 31, w >> 20 & 31, w >> 25
        a, b = x[rs1], x[rs2]
        nxt, val = pc + 4, None
        if op == 0x33:
            val = {(0, 0): a + b, (0x20, 0): a - b, (0, 1): a << (b & 31),
                   (0, 2): int(_s32(a) < _s32(b)), (0, 4): a ^ b,
                   (0, 5): a >> (b & 31), (0, 6): a | b, (0, 7): a & b}[(f7, f3)]
        elif op == 0x13:
            imm = _sx(w >> 20, 12)
            if f3 == 1:
                val = a << rs2
            elif f3 == 5:
                val = a >> rs2
            else:
                val = {0: a + imm, 2: int(_s32(a) < imm), 4: a ^ (imm & MASK),
                       6: a | (imm & MASK), 7: a & (imm & MASK)}[f3]
        elif op == 0x03:
            val = mem[((a + _sx(w >> 20, 12)) & MASK) >> 2]
        elif op == 0x23:
            imm = _sx((w >> 25) << 5 | rd, 12)
            mem[((a + imm) & MASK) >> 2] = b
            rd = 0
        elif op == 0x63:
            imm = _sx((w >> 31) << 12 | (w >> 7 & 1) << 11 | (w >> 25 & 63) << 5
                      | (w >> 8 & 15) << 1, 13)
            take = {0: a == b, 1: a != b, 4: _s32(a) < _s32(b),
                    5: _s32(a) >= _s32(b)}[f3]
            if take:
                nxt = pc + imm
            rd = 0
        elif op == 0x37:
            val = w & 0xFFFFF000
        elif op == 0x6F:
            imm = _sx((w >> 31) << 20 | (w >> 12 & 0xFF) << 12 | (w >> 20 & 1) << 11
                      | (w >> 21 & 0x3FF) << 1, 21)
            val, nxt = pc + 4, pc + imm
        else:
            raise ValueError(f"ISS: unsupported opcode {op:#x} at {pc:#x}")
        if val is not None and rd:
            x[rd] = val & MASK
        pc, n = nxt & MASK, n + 1
    return n, mem


# ── workloads ──────────────────────────────────────────
# Register conventions: x1 = data base; results at DATA_BASE + 0x800...

def _li(rd, value):
    """lui+addi pair (always two instructions, keeps code size fixed)."""
    hi = (value + 0x800) >> 12
    lo = value - (hi << 12)
    return [("lui", rd, hi & 0xFFFFF), ("addi", rd, rd, lo)]


def _prologue():
    return _li("x1", DATA_BASE) + _li("x2", DATA_BASE + 0x800)


def w_sum(n):
    return _prologue() + [
        ("addi", "x3", "x0", 0), ("addi", "x4", "x0", n), ("addi", "x5", "x1", 0),
        "loop", ("lw", "x6", 0, "x5"), ("add", "x3", "x3", "x6"),
        ("addi", "x5", "x5", 4), ("addi", "x4", "x4", -1), ("bne", "x4", "x0", "loop"),
        ("sw", "x3", 0, "x2"), "halt", ("jal", "x0", "halt")]


def w_fib(n):
    return _prologue() + [
        ("addi", "x3", "x0", 0), ("addi", "x4", "x0", 1), ("addi", "x5", "x0", n),
        ("lw", "x7", 0, "x1"), ("add", "x3", "x3", "x7"),     # seed-dependent start
        "loop", ("sw", "x3", 0, "x2"), ("add", "x6", "x3", "x4"),
        ("addi", "x3", "x4", 0), ("addi", "x4", "x6", 0), ("addi", "x2", "x2", 4),
        ("addi", "x5", "x5", -1), ("bne", "x5", "x0", "loop"),
        "halt", ("jal", "x0", "halt")]


def w_bubble(n):
    # in-place bubble sort (signed) of n words at x1
    return _prologue() + [
        ("addi", "x3", "x0", n - 1),                       # outer count
        "outer", ("addi", "x5", "x1", 0), ("addi", "x4", "x3", 0),
        "inner", ("lw", "x6", 0, "x5"), ("lw", "x7", 4, "x5"),
        ("bge", "x7", "x6", "noswap"),
        ("sw", "x7", 0, "x5"), ("sw", "x6", 4, "x5"),
        "noswap", ("addi", "x5", "x5", 4), ("addi", "x4", "x4", -1),
        ("bne", "x4", "x0", "inner"),
        ("addi", "x3", "x3", -1), ("bne", "x3", "x0", "outer"),
        "halt", ("jal", "x0", "halt")]


def w_mul(pairs):
    # shift-add multiply of word pairs (a, b) -> low 32 bits
    return _prologue() + [
        ("addi", "x9", "x0", pairs),
        "next", ("lw", "x3", 0, "x1"), ("lw", "x4", 4, "x1"), ("addi", "x5", "x0", 0),
        "bit", ("beq", "x4", "x0", "store"),
        ("andi", "x6", "x4", 1), ("beq", "x6", "x0", "skip"),
        ("add", "x5", "x5", "x3"),
        "skip", ("slli", "x3", "x3", 1), ("srli", "x4", "x4", 1),
        ("jal", "x0", "bit"),
        "store", ("sw", "x5", 0, "x2"), ("addi", "x1", "x1", 8), ("addi", "x2", "x2", 4),
        ("addi", "x9", "x9", -1), ("bne", "x9", "x0", "next"),
        "halt", ("jal", "x0", "halt")]


def w_popcount(n):
    return _prologue() + [
        ("addi", "x9", "x0", n), ("addi", "x8", "x0", 0),
        "next", ("lw", "x3", 0, "x1"), ("addi", "x5", "x0", 0),
        "bit", ("beq", "x3", "x0", "done"),
        ("andi", "x6", "x3", 1), ("add", "x5", "x5", "x6"), ("srli", "x3", "x3", 1),
        ("jal", "x0", "bit"),
        "done", ("sw", "x5", 0, "x2"), ("add", "x8", "x8", "x5"),
        ("addi", "x1", "x1", 4), ("addi", "x2", "x2", 4),
        ("addi", "x9", "x9", -1), ("bne", "x9", "x0", "next"),
        ("sw", "x8", 0, "x2"), "halt", ("jal", "x0", "halt")]


def w_checksum(n):
    # rotate-xor-add checksum using sll/srl/or/xor/slt
    return _prologue() + [
        ("addi", "x3", "x0", 0), ("addi", "x9", "x0", n), ("addi", "x10", "x0", 0),
        ("addi", "x11", "x0", 5), ("addi", "x12", "x0", 27),
        "loop", ("lw", "x4", 0, "x1"),
        ("sll", "x5", "x3", "x11"), ("srl", "x6", "x3", "x12"), ("or", "x3", "x5", "x6"),
        ("xor", "x3", "x3", "x4"), ("slt", "x7", "x4", "x0"), ("add", "x10", "x10", "x7"),
        ("sub", "x3", "x3", "x7"),
        ("addi", "x1", "x1", 4), ("addi", "x9", "x9", -1), ("bne", "x9", "x0", "loop"),
        ("sw", "x3", 0, "x2"), ("sw", "x10", 4, "x2"), "halt", ("jal", "x0", "halt")]


# name -> (program, number of input words, output words to check)
WORKLOADS = {
    "sum64":      (lambda: w_sum(64), 64, 1),
    "fib40":      (lambda: w_fib(40), 1, 40),
    "bubble24":   (lambda: w_bubble(24), 24, 0),     # checks the sorted array
    "mul16":      (lambda: w_mul(16), 32, 16),
    "popcount32": (lambda: w_popcount(32), 32, 33),
    "checksum96": (lambda: w_checksum(96), 96, 2),
}
VISIBLE = ("sum64", "fib40")
HELD_OUT = ("bubble24", "mul16", "popcount32", "checksum96")


def input_words(name, seed, count):
    rng = random.Random(f"{name}:{seed}")
    if name == "mul16":
        return [rng.getrandbits(rng.choice((8, 12, 16))) for _ in range(count)]
    if name == "fib40":
        return [rng.randrange(0, 1000)]
    return [rng.getrandbits(32) for _ in range(count)]


def build(name, seed):
    make, n_in, n_out = WORKLOADS[name]
    words, labels = assemble(make())
    mem = [0] * MEM_WORDS
    data = input_words(name, seed, n_in)
    base = DATA_BASE >> 2
    mem[base:base + n_in] = data
    init = list(mem)
    retired, final = iss(words, mem, labels["halt"])
    out_base = (DATA_BASE + 0x800) >> 2
    if name == "bubble24":
        checks = [(base + i, final[base + i]) for i in range(n_in)]
        assert [_s32(v) for _, v in checks] == sorted(_s32(v) for v in data)
    else:
        checks = [(out_base + i, final[out_base + i]) for i in range(n_out)]
    return {"words": words, "halt": labels["halt"], "init": init, "data_base": base,
            "n_in": n_in, "checks": checks, "instructions": retired}


# ── testbench generation ───────────────────────────────

def generate_tb(seeds=SEEDS) -> str:
    out = []
    emit = out.append
    emit("// GENERATED by pipeline/riscv_workloads.py -- do not edit by hand.\n"
         "// Programs, inputs, expected signatures and ISS instruction counts are\n"
         "// produced by the host ISA model; this bench measures RTL cycles.\n"
         "`timescale 1ns/1ps\n"
         "module riscv_perf_tb;\n"
         "  reg clk = 0, reset = 1;\n"
         "  wire [31:0] pc, aluout, writedata;\n"
         "  wire memwrite, memread, suspend;\n"
         f"  reg [31:0] imem [0:255];\n  reg [31:0] dmem [0:{MEM_WORDS - 1}];\n"
         "  wire [31:0] instr = imem[pc[9:2]];\n"
         f"  wire [31:0] readdata = dmem[aluout[{(MEM_WORDS - 1).bit_length() + 1}:2]];\n"
         "  riscv dut (.clk(clk), .reset(reset), .pc(pc), .instr(instr),\n"
         "             .memwrite(memwrite), .aluout(aluout), .writedata(writedata),\n"
         "             .readdata(readdata), .memread(memread), .suspend(suspend));\n"
         "  always #5 clk = ~clk;\n"
         "  always @(posedge clk) if (!reset && memwrite)\n"
         f"    dmem[aluout[{(MEM_WORDS - 1).bit_length() + 1}:2]] <= writedata;\n"
         "  integer seed, checks, cycles, i;\n")
    emit("  task automatic run(input [31:0] halt_pc, input integer limit, output integer n);\n"
         "    begin\n"
         "      reset = 1; @(posedge clk); @(posedge clk); #1 reset = 0; n = 0;\n"
         "      while (pc !== halt_pc) begin\n"
         "        @(posedge clk); #1 n = n + 1;\n"
         "        if (n > limit) $fatal(1, \"timeout before halt\");\n"
         "      end\n"
         "    end\n  endtask\n")
    emit("  task automatic expect_word(input integer idx, input [31:0] value, input [8*16-1:0] wl);\n"
         "    begin\n"
         "      if (dmem[idx] !== value)\n"
         "        $fatal(1, \"%0s: mem[%0d]=%h expected %h\", wl, idx, dmem[idx], value);\n"
         "      checks = checks + 1;\n"
         "    end\n  endtask\n")
    emit("  initial begin\n"
         "    checks = 0;\n"
         "    if (!$value$plusargs(\"seed=%d\", seed)) seed = 1;\n"
         "    case (seed)\n")
    for seed in seeds:
        emit(f"      {seed}: begin\n")
        for name in WORKLOADS:
            b = build(name, seed)
            emit(f"        // {name}\n"
                 f"        for (i = 0; i < 256; i = i + 1) imem[i] = 32'h0;\n"
                 f"        for (i = 0; i < {MEM_WORDS}; i = i + 1) dmem[i] = 32'h0;\n")
            for i, w in enumerate(b["words"]):
                emit(f"        imem[{i}] = 32'h{w:08x};\n")
            for i in range(b["n_in"]):
                emit(f"        dmem[{b['data_base'] + i}] = 32'h{b['init'][b['data_base'] + i]:08x};\n")
            emit(f"        run(32'h{b['halt']:08x}, {b['instructions'] * 4 + 100}, cycles);\n")
            for idx, val in b["checks"]:
                emit(f"        expect_word({idx}, 32'h{val:08x}, \"{name}\");\n")
            emit(f"        $display(\"PPA_PERF workload={name} instructions={b['instructions']} cycles=%0d\", cycles);\n")
        emit("      end\n")
    emit("      default: $fatal(1, \"seed has no generated workloads\");\n"
         "    endcase\n"
         "    $display(\"PPA_REGRESSION_PASS checks=%0d\", checks);\n"
         "    $finish;\n  end\nendmodule\n")
    return "".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(generate_tb(), encoding="utf-8")
    for name in WORKLOADS:
        b = build(name, SEEDS[0])
        tag = "held-out" if name in HELD_OUT else "visible"
        print(f"{name:11s} {tag:8s} {b['instructions']:6d} instr  {len(b['checks'])} checks")


if __name__ == "__main__":
    main()
