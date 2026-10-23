"""Host-side reference models: independent of the HDL scoreboard, not hidden VIP."""
import re


def counter4_workload(seed):
    vectors = [(1, 0)] + [(0, 1)] * 48 + [(0, 0)] * 16 + [(1, 1)]
    state = seed
    for _ in range(256):
        state ^= (state << 13) & 0xffffffff
        state ^= state >> 17
        state ^= (state << 5) & 0xffffffff
        vectors.append((int(state & 31 == 0), (state >> 8) & 1))
    return vectors


def counter4(output, expected_checks, seed):
    lines = [line for line in output.splitlines() if line.startswith("PPA_TRACE ")]
    vectors = counter4_workload(seed)
    if expected_checks != len(vectors) or len(lines) != expected_checks:
        return {"status": "unverified", "reason": "missing or incomplete cycle trace"}
    state = None
    for cycle, line in enumerate(lines):
        match = re.fullmatch(r"PPA_TRACE cycle=(\d+) rst=([01]) en=([01]) count=(\d+)", line)
        if not match or int(match[1]) != cycle:
            return {"status": "unverified", "reason": f"invalid trace at cycle {cycle}"}
        reset, enable, observed = map(int, match.groups()[1:])
        if (reset, enable) != vectors[cycle]:
            return {"status": "failed", "cycle": cycle, "reason": "stimulus differs from canonical workload"}
        if reset:
            state = 0
        elif state is None:
            return {"status": "unverified", "reason": "trace must establish state with reset"}
        elif enable:
            state = (state + 1) % 16
        if observed != state:
            return {"status": "failed", "cycle": cycle, "expected": state, "observed": observed}
    return {"status": "passed", "checks": len(lines)}


def sram_wrapper(output, expected_checks, seed):
    """Model wrapper registers and OpenRAM's posedge latch/negedge memory protocol.

    Never assert a value for unwritten memory or same-address write/read collision.
    SRAM contents persist across wrapper reset.
    """
    lines = [line for line in output.splitlines() if line.startswith("PPA_SRAM ")]
    if expected_checks != 800 or len(lines) != 800:
        return {"status": "unverified", "reason": "requires all 800 SRAM trace cycles"}
    memory = [None] * 256
    address = previous = None
    expected = [None, None]
    checked = [0, 0]
    skipped = [0, 0]
    rng = seed
    for cycle, line in enumerate(lines):
        match = re.fullmatch(r"PPA_SRAM cycle=(\d+) rst=([01]) dout0=([0-9a-fA-FxXzZ]+) dout1=([0-9a-fA-FxXzZ]+)", line)
        if not match or int(match[1]) != cycle:
            return {"status": "unverified", "reason": f"invalid trace at cycle {cycle}"}
        rng ^= (rng << 13) & 0xffffffff
        rng ^= rng >> 17
        rng ^= (rng << 5) & 0xffffffff
        reset = int(cycle < 2 or cycle in (300, 620) or (cycle >= 650 and rng & 31 == 0))
        if int(match[2]) != reset:
            return {"status": "failed", "cycle": cycle, "reason": "reset stimulus differs"}
        for port, raw in enumerate(match.groups()[2:]):
            if expected[port] is None:
                skipped[port] += 1
                continue
            checked[port] += 1
            if any(c in raw.lower() for c in "xz") or int(raw, 16) != expected[port]:
                return {"status": "failed", "cycle": cycle, "port": port,
                        "expected": expected[port], "observed": raw}
        # Outputs captured at this posedge came from the preceding falling edge.
        # Predict the next capture from the addresses latched before NBA updates.
        write = address is not None and address % 2 == 0
        expected = [memory[address] if address is not None and not write else None,
                    memory[previous] if previous is not None and not (write and previous == address) else None]
        if write:
            memory[address] = address
        address, previous = ((0, 0) if reset else
                             ((address + 1) % 256 if address is not None else None, address))
    return {"status": "passed" if checked[1] > 0 else "unverified", "checks": sum(checked),
            "trace_cycles": 800, "checked_by_port": checked, "undefined_by_port": skipped,
            "scope": "Defined wrapper outputs only; unwritten addresses and write/read collisions excluded"}


ORACLES = {"counter4": counter4, "sram_wrapper": sram_wrapper}
