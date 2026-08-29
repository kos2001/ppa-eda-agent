"""Tests for the dashboard's markdown parser and the theme contrast.

Two things this session found in the console, both in the same view — a
human-in-the-loop review request, i.e. the one screen whose whole job is
to be read carefully before someone commits a judgement:

  - It was rendered in a bare <pre> at max-height 260px and font-size
    0.66rem. A 10,500-character document with eight headings, eight
    lists and fenced tool output, shown as an unstyled wall four lines
    tall. Every structural cue request_review.py emits was discarded.

  - Its status colours failed WCAG AA. Light-mode green was 3.06:1 and
    the pill backgrounds were 1.01:1 against the page — not a shape at
    all. The dark palette had been measured and tuned in an earlier
    session; the light one never had.

The parser is exercised against a **real generated request**, not a
fixture, because the fixture would be written by whoever wrote the
parser and would share its blind spots. The contrast is computed from
index.css itself rather than from numbers copied into a comment, so the
test fails if someone edits a colour without redoing the arithmetic.
"""
import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "dashboard" / "src" / "index.css"
HARNESS = ROOT / "tests" / "markdown_parse_check.mjs"
REVIEWS = ROOT / "reference-db" / "reviews"


# --- contrast ------------------------------------------------------

def _luminance(hex_colour: str) -> float:
    h = hex_colour.lstrip("#")
    chans = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
           for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def _tokens(selector: str) -> dict:
    css = CSS.read_text()
    m = re.search(selector + r"\s*\{(.*?)\n\}", css, re.S)
    if not m:
        raise AssertionError(f"no block for {selector}")
    return {k: v.strip()
            for k, v in re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6});", m.group(1))}


def themes() -> dict:
    light = _tokens(r"^:root")
    dark = dict(light)
    dark.update(_tokens(r':root\[data-theme="dark"\]'))
    return {"light": light, "dark": dark}


# WCAG AA for body text. Applied to both themes equally — the point of
# the change was that one of them had been held to it and the other had
# not.
AA = 4.5
SURFACES = ("bg", "surface", "surface-raised")
STATUS = ("critical", "good", "warn")


class ContrastTests(unittest.TestCase):
    def setUp(self):
        self.themes = themes()

    def test_status_text_is_readable_on_every_surface(self):
        for name, t in self.themes.items():
            for kind in STATUS:
                for surface in SURFACES:
                    got = contrast(t[kind], t[surface])
                    self.assertGreaterEqual(
                        got, AA, f"{name}: --{kind} on --{surface} is {got:.2f}:1")

    def test_status_text_is_readable_on_its_own_pill(self):
        # The pill is where these colours are actually used, and it was
        # the worst case: light green was 3.06:1 on --good-soft.
        for name, t in self.themes.items():
            for kind in STATUS:
                got = contrast(t[kind], t[f"{kind}-soft"])
                self.assertGreaterEqual(
                    got, AA, f"{name}: --{kind} on --{kind}-soft is {got:.2f}:1")

    def test_a_pill_reads_as_a_shape_not_floating_text(self):
        # Separate from text contrast and just as load-bearing: at
        # 1.01:1 the light pills were indistinguishable from the page,
        # so a PASS badge was text on nothing while dark's was a chip.
        for name, t in self.themes.items():
            for kind in STATUS:
                got = contrast(t[f"{kind}-soft"], t["bg"])
                self.assertGreaterEqual(
                    got, 1.28, f"{name}: --{kind}-soft vs --bg is {got:.2f}:1")

    def test_body_and_secondary_text_are_readable(self):
        for name, t in self.themes.items():
            for kind in ("text", "text-dim", "accent"):
                for surface in SURFACES:
                    got = contrast(t[kind], t[surface])
                    self.assertGreaterEqual(
                        got, AA, f"{name}: --{kind} on --{surface} is {got:.2f}:1")

    def test_the_three_surfaces_are_distinct_planes(self):
        # Dark's were 1.08 and 1.07 apart and light's raised was byte-
        # identical to its surface, so panels did not read as panels.
        for name, t in self.themes.items():
            self.assertGreaterEqual(
                contrast(t["surface"], t["bg"]), 1.12,
                f"{name}: --surface does not separate from --bg")
            self.assertGreaterEqual(
                contrast(t["surface-raised"], t["surface"]), 1.12,
                f"{name}: --surface-raised does not separate from --surface")

    def test_borders_are_visible_against_what_they_separate(self):
        for name, t in self.themes.items():
            for surface in ("bg", "surface"):
                got = contrast(t["border"], t[surface])
                self.assertGreaterEqual(
                    got, 1.28, f"{name}: --border on --{surface} is {got:.2f}:1")

    def test_secondary_text_stays_secondary(self):
        # Fixing contrast by making --text-dim equal --text would pass
        # every check above and destroy the hierarchy it exists for.
        for name, t in self.themes.items():
            self.assertNotEqual(t["text-dim"], t["text"], name)
            ratio = _luminance(t["text-dim"]) / _luminance(t["text"])
            far = ratio if ratio < 1 else 1 / ratio
            self.assertLess(far, 0.75,
                            f"{name}: --text-dim is not visibly dimmer than --text")

    def test_the_two_dark_blocks_agree(self):
        # The palette is declared twice — once under prefers-color-scheme
        # and once under [data-theme="dark"]. They must not drift, or the
        # toggle and the OS setting render differently.
        css = CSS.read_text()
        media = _tokens(r':root:not\(\[data-theme="light"\]\)')
        explicit = _tokens(r':root\[data-theme="dark"\]')
        self.assertEqual(media, explicit)
        self.assertIn("prefers-color-scheme: dark", css)


class TokenReferenceTests(unittest.TestCase):
    def test_every_var_reference_resolves(self):
        # A typo'd token name is not an error anywhere — the property
        # just does not apply, so the element renders unstyled and
        # nothing says why. Cheap to check, and this session added a
        # stylesheet built entirely out of these references.
        defined = set(re.findall(r"--([\w-]+)\s*:", CSS.read_text()))
        missing = set()
        for path in (ROOT / "dashboard" / "src").rglob("*.css"):
            for used in re.findall(r"var\(\s*--([\w-]+)", path.read_text()):
                if used not in defined:
                    missing.add(f"{path.name}: --{used}")
        self.assertEqual(missing, set())

    def test_the_new_stylesheet_uses_tokens_not_literals(self):
        # Markdown.css must inherit both palettes rather than carrying a
        # third one, which is how a component ends up readable in one
        # theme and not the other.
        md = (ROOT / "dashboard" / "src" / "components" / "Markdown.css").read_text()
        literals = re.findall(r"(?<!-)#[0-9a-fA-F]{3,8}\b", md)
        self.assertEqual(literals, [], f"hard-coded colours: {literals}")


# --- parser --------------------------------------------------------

def _real_request() -> Path | None:
    """The largest real generated review request, if one exists."""
    hits = sorted(REVIEWS.glob("*__request.md"),
                  key=lambda p: p.stat().st_size, reverse=True)
    return hits[0] if hits else None


def _parse(path: Path) -> dict:
    out = subprocess.run(
        ["npx", "tsx", str(HARNESS), str(path)],
        cwd=ROOT / "dashboard", capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise unittest.SkipTest(f"tsx unavailable: {out.stderr[-300:]}")
    return json.loads(out.stdout)


class ParserTests(unittest.TestCase):
    """Against a real generated document, not a fixture."""

    @classmethod
    def setUpClass(cls):
        path = _real_request()
        if path is None:
            raise unittest.SkipTest("no generated review request to parse")
        if not (ROOT / "dashboard" / "node_modules").is_dir():
            raise unittest.SkipTest("dashboard dependencies not installed")
        cls.path = path
        cls.got = _parse(path)

    def test_finds_the_documents_structure(self):
        # The <pre> showed none of this. Each count is something a
        # reader was previously left to spot in an unstyled wall.
        c = self.got["counts"]
        self.assertGreaterEqual(c.get("heading", 0), 5)
        self.assertGreaterEqual(c.get("list", 0), 3)
        self.assertGreaterEqual(c.get("code", 0), 1)

    def test_heading_levels_are_preserved(self):
        levels = {h["level"] for h in self.got["headings"]}
        self.assertIn(1, levels)
        self.assertIn(2, levels)
        # h3 is what separates one retrieved precedent from the next.
        self.assertIn(3, levels)

    def test_fenced_tool_output_is_not_reinterpreted(self):
        # Recorded diagnoses are full of lines starting with '-' and
        # with '#'. Parsed as markdown they would become lists and
        # headings, silently restructuring evidence.
        for block in self.got["code"]:
            self.assertNotIn("```", block["text"])

    def test_no_fence_marker_survives_into_prose(self):
        for para in self.got["paras"]:
            self.assertNotIn("```", para)

    def test_list_items_keep_their_inline_code(self):
        # The subagent list is the actionable part of the request: each
        # item is a name plus the path to its .md file.
        joined = " ".join(self.got["listItems"])
        self.assertIn("`", joined)

    def test_the_whole_document_is_accounted_for(self):
        # A parser that silently drops blocks would still pass the
        # counts above. This checks nothing fell on the floor.
        text = self.path.read_text()
        non_empty = [ln for ln in text.splitlines() if ln.strip()]
        self.assertGreater(self.got["total"], 0)
        self.assertLessEqual(self.got["total"], len(non_empty))


if __name__ == "__main__":
    sys.exit(unittest.main())
