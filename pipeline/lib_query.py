"""Reads Liberty timing models directly, so a diagnosis can be checked
against the cell library instead of argued about.

Why this exists: sram_wrapper sat open for five days on the conclusion
that a macro's 0.04 ns max_transition was "physically floored" — that no
cell could meet it, so there was nothing to try. Four reviews argued
over that from the one number OpenROAD had printed. The liberty file was
on disk the whole time and settled it in one query: the fastest cell in
sky130_fd_sc_hd produces 19.3 ps at that pin's load, half the limit. The
43 ps OpenROAD reported was just the PDK's default RE_BUFFER_CELL
(buf_4) doing its best, not a property of the technology.

The pipeline could already query placement (odb_query), timing
(sta_report) and function (equiv_check). It could not read the cell
models, which is why that particular wrong conclusion survived so long.

Deliberately a small hand-rolled parser rather than a liberty library:
this needs the transition tables and pin capacitances, nothing else, and
the project stays dependency-free (soul.md).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Liberty declares its own units and they are not always ns/pF. Every
# number this module returns is meaningless without them, so they are
# parsed and checked rather than assumed.
_TIME_UNIT = re.compile(r'time_unit\s*:\s*"?([\d.]+)(\w+)"?\s*;')
_CAP_UNIT = re.compile(r'capacitive_load_unit\s*\(\s*([\d.]+)\s*,\s*"?(\w+)"?\s*\)')

_NUM = re.compile(r"-?\d+\.?\d*(?:[eE][-+]?\d+)?")


class LibertyError(RuntimeError):
    """Raised rather than returning a plausible-looking default — a
    silently wrong timing number is worse than no number."""


def _nums(text: str) -> list[float]:
    return [float(x) for x in _NUM.findall(text.replace('"', " "))]


@dataclass(frozen=True)
class Table:
    """A 2-D liberty lookup table: input slew x output load -> value."""

    slews: list[float]
    loads: list[float]
    values: list[list[float]]

    def at(self, slew: float, load: float) -> float:
        """Bilinear interpolation, clamped at the table edges.

        Clamping (rather than extrapolating) is the conservative choice:
        outside the characterised range the vendor made no claim, and a
        made-up extrapolated number would read as measurement.
        """

        def pos(axis: list[float], x: float) -> tuple[int, float]:
            if x <= axis[0]:
                return 0, 0.0
            if x >= axis[-1]:
                return len(axis) - 2, 1.0
            for i in range(len(axis) - 1):
                if axis[i] <= x <= axis[i + 1]:
                    return i, (x - axis[i]) / (axis[i + 1] - axis[i])
            return len(axis) - 2, 1.0

        i, fi = pos(self.slews, slew)
        j, fj = pos(self.loads, load)
        v = self.values
        return (
            v[i][j] * (1 - fi) * (1 - fj)
            + v[i + 1][j] * fi * (1 - fj)
            + v[i][j + 1] * (1 - fi) * fj
            + v[i + 1][j + 1] * fi * fj
        )

    @property
    def min_slew(self) -> float:
        return self.slews[0]

    @property
    def max_load(self) -> float:
        return self.loads[-1]


@dataclass(frozen=True)
class Library:
    path: Path
    time_unit_ns: float
    cap_unit_pf: float
    transitions: dict[str, Table]
    pin_caps: dict[str, float]
    default_max_transition: float | None

    def drive_cells(self) -> list[str]:
        return sorted(self.transitions)


def _parse_units(txt: str, path: Path) -> tuple[float, float]:
    tm = _TIME_UNIT.search(txt)
    cm = _CAP_UNIT.search(txt)
    if not tm or not cm:
        raise LibertyError(f"{path}: missing time_unit or capacitive_load_unit")
    scale_t, unit_t = float(tm.group(1)), tm.group(2).lower()
    scale_c, unit_c = float(cm.group(1)), cm.group(2).lower()
    to_ns = {"ns": 1.0, "ps": 1e-3, "us": 1e3}
    to_pf = {"pf": 1.0, "ff": 1e-3, "nf": 1e3}
    if unit_t not in to_ns or unit_c not in to_pf:
        raise LibertyError(f"{path}: unsupported units {unit_t}/{unit_c}")
    return scale_t * to_ns[unit_t], scale_c * to_pf[unit_c]


def _parse_table(body: str) -> Table | None:
    """A transition group only carries real drive data when it declares
    its own axes; templates and degenerate tri-state-disable groups do
    not, and including those is how a naive scan reports a 0 ps floor."""
    if "index_1" not in body or "index_2" not in body:
        return None
    i1 = re.search(r"index_1\s*\((.*?)\)\s*;", body, re.S)
    i2 = re.search(r"index_2\s*\((.*?)\)\s*;", body, re.S)
    vm = re.search(r"values\s*\((.*?)\)\s*;", body, re.S)
    if not (i1 and i2 and vm):
        return None
    slews, loads = _nums(i1.group(1)), _nums(i2.group(1))
    rows = [_nums(r) for r in re.findall(r'"([^"]*)"', vm.group(1))]
    if len(rows) != len(slews) or any(len(r) != len(loads) for r in rows):
        return None
    if len(slews) < 2 or len(loads) < 2:
        return None
    return Table(slews, loads, rows)


def load_library(path: Path | str) -> Library:
    """Parses one .lib. Reads rise_transition tables and input pin
    capacitances — the two things a slew argument actually turns on."""
    path = Path(path)
    if not path.is_file():
        raise LibertyError(f"liberty file not found: {path}")
    txt = path.read_text(errors="replace")
    time_unit_ns, cap_unit_pf = _parse_units(txt, path)

    dm = re.search(r"default_max_transition\s*:\s*([\d.]+)\s*;", txt)
    default_max = float(dm.group(1)) * time_unit_ns if dm else None

    transitions: dict[str, Table] = {}
    pin_caps: dict[str, float] = {}
    for chunk in re.split(r"\n\s*cell \(", txt)[1:]:
        try:
            name = chunk[: chunk.index(")")].strip().strip('"')
        except ValueError:
            continue
        m = re.search(r"rise_transition\s*\(\s*\"?\w+\"?\s*\)\s*\{(.*?)\n\s*\}",
                      chunk, re.S)
        if m:
            tbl = _parse_table(m.group(1))
            if tbl:
                transitions[name] = Table(
                    [s * time_unit_ns for s in tbl.slews],
                    [c * cap_unit_pf for c in tbl.loads],
                    [[v * time_unit_ns for v in row] for row in tbl.values],
                )
        for pm in re.finditer(
            r"pin\s*\(\s*([\w\[\]]+)\s*\)\s*\{(.*?)capacitance\s*:\s*([\d.eE+-]+)",
            chunk, re.S
        ):
            pin_caps[f"{name}/{pm.group(1)}"] = float(pm.group(3)) * cap_unit_pf

    if not transitions:
        raise LibertyError(f"{path}: no usable rise_transition tables found")
    return Library(path, time_unit_ns, cap_unit_pf, transitions, pin_caps,
                   default_max)


def pin_capacitance(path: Path | str, pin: str) -> float:
    """Input capacitance of `cell/pin`, in pF."""
    lib = load_library(path)
    if pin not in lib.pin_caps:
        raise LibertyError(f"{path}: no capacitance for pin {pin!r}")
    return lib.pin_caps[pin]


def transition_at(lib: Library, cell: str, load_pf: float,
                  input_slew_ns: float | None = None) -> float:
    """Output transition (ns) of `cell` driving `load_pf`.

    input_slew_ns defaults to the cell's fastest characterised slew,
    i.e. the best case. Pass a realistic degraded slew to find out
    whether a cell still holds up in situ — buffers differ sharply here,
    which is the difference between a fix that works and one that does
    not.
    """
    if cell not in lib.transitions:
        raise LibertyError(f"{lib.path}: no transition table for {cell!r}")
    tbl = lib.transitions[cell]
    return tbl.at(tbl.min_slew if input_slew_ns is None else input_slew_ns,
                  load_pf)


def cells_meeting(lib: Library, limit_ns: float, load_pf: float,
                  input_slew_ns: float | None = None) -> list[tuple[str, float]]:
    """Every cell whose output transition meets `limit_ns` at this load,
    fastest first. An empty list is the real evidence for "unmeetable";
    a non-empty one refutes it."""
    out = []
    for cell in lib.transitions:
        t = transition_at(lib, cell, load_pf, input_slew_ns)
        if t <= limit_ns:
            out.append((cell, t))
    return sorted(out, key=lambda kv: kv[1])


def max_wire_um(lib: Library, cell: str, limit_ns: float, pin_cap_pf: float,
                cap_per_um_pf: float, input_slew_ns: float | None = None,
                ceiling_um: float = 5000.0) -> float:
    """Longest wire `cell` can drive into `pin_cap_pf` within `limit_ns`.

    Turns "keep the driver adjacent to the macro" — which a placer cannot
    act on — into a distance a floorplan can be checked against.
    Returns 0.0 when the cell misses the limit at zero wire.
    """
    if transition_at(lib, cell, pin_cap_pf, input_slew_ns) > limit_ns:
        return 0.0
    lo, hi = 0.0, ceiling_um
    for _ in range(60):
        mid = (lo + hi) / 2
        t = transition_at(lib, cell, pin_cap_pf + mid * cap_per_um_pf,
                          input_slew_ns)
        if t <= limit_ns:
            lo = mid
        else:
            hi = mid
    return lo


def wire_cap_per_um(tlef: Path | str, layer: str) -> float:
    """pF/µm for one routing layer, from the tech LEF's own numbers
    (plate + both edges). Wire capacitance is what decides whether a
    placement distance is survivable, so it is read from the PDK rather
    than taken from a rule of thumb."""
    tlef = Path(tlef)
    if not tlef.is_file():
        raise LibertyError(f"tech LEF not found: {tlef}")
    txt = tlef.read_text(errors="replace")
    m = re.search(rf"^LAYER {re.escape(layer)}\s*$(.*?)^END {re.escape(layer)}",
                  txt, re.S | re.M)
    if not m:
        raise LibertyError(f"{tlef}: no LAYER {layer}")
    body = m.group(1)
    if not re.search(r"TYPE\s+ROUTING", body):
        raise LibertyError(f"{tlef}: layer {layer} is not TYPE ROUTING")

    def one(pat: str) -> float:
        mm = re.search(pat, body, re.M)
        if not mm:
            raise LibertyError(f"{tlef}: layer {layer} missing {pat!r}")
        return float(mm.group(1))

    width = one(r"^\s*WIDTH\s+([\d.]+)\s*;")
    area = one(r"^\s*CAPACITANCE CPERSQDIST\s+([\d.eE+-]+)\s*;")
    edge = one(r"^\s*EDGECAPACITANCE\s+([\d.eE+-]+)\s*;")
    return width * area + 2 * edge
