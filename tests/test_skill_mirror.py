"""The project skill must read the same to every agent.

`.codex/skills/ppa-eda-flow` is the source. Codex discovers it there;
Claude Code discovers skills only under `.claude/skills/`, so a Claude
session never saw the experiment workflow AGENTS.md points to. The copy
is a copy rather than a symlink because this repository is also checked
out on Windows, where a symlink becomes a one-line text file. A copy can
drift, and two workflows that disagree are worse than one missing: this
fails on the first byte that differs, and on a file present on one side.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / ".codex" / "skills" / "ppa-eda-flow"
MIRROR = ROOT / ".claude" / "skills" / "ppa-eda-flow"


def files(root: Path) -> dict[str, bytes]:
    # Hidden files and bytecode are local litter (Finder's .DS_Store,
    # __pycache__), never skill content; counting them fails the test on
    # a machine where nothing tracked differs.
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()
            and not any(part.startswith(".") or part == "__pycache__"
                        for part in p.relative_to(root).parts)}


class SkillMirrorTests(unittest.TestCase):
    def test_claude_copy_matches_the_codex_source(self):
        source, mirror = files(SOURCE), files(MIRROR)
        self.assertTrue(source, "the source skill is missing")
        self.assertEqual(sorted(source), sorted(mirror),
                         "file lists differ; copy .codex/skills/ppa-eda-flow over "
                         ".claude/skills/ppa-eda-flow")
        for name in source:
            self.assertEqual(source[name], mirror[name],
                             f"{name} differs; edit the .codex source and copy it")


if __name__ == "__main__":
    unittest.main()
