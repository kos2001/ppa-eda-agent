#!/usr/bin/env python3
r"""Retrieval for free-form questions about this service.

The console could answer exactly one kind of question — paste a report,
get a diagnosis. "What is this thing for?", "why gf180 as well as
sky130?", "what has counter4 actually passed?" had no surface, and all
three are answerable from what the repo already holds: the first two
from documents it writes anyway, the third from 400 recorded samples.

TWO STEPS, NOT ONE. Retrieval returns passages; generation turns them
into a sentence. They are separate because the first is the useful half
and needs no model: a checkout with no hermes-gateway key still gets
"here is the section that answers this, in README.md", which is a worse
answer than a written one and a much better answer than a blank box.

NO EMBEDDINGS, for the reason case_retrieval.py records about its own
exact-match keys: a score nobody can read is a score nobody can correct.
Ranking here is term overlap weighted by how rare each term is across
the corpus, and every hit reports which terms matched. When a wrong
passage comes back, the matched terms say why, and the fix is a term
list rather than a retraining run.

The rare-term weighting is not decoration. The three reference documents
(report-area, report-timing, report-power) share most of their
vocabulary, and raw term counts rank the longest of them first for every
EDA question — a retrieval layer that has quietly become a constant.
Weighting by rarity is what separates them, and a test pins it.

NOTHING ABOUT THE STORE IS WRITTEN DOWN HERE. Counts come from
reference-db at call time. The progress page had "441 rows" typed into a
sentence and it was wrong within a day of being written; the same
sentence computed is right forever.
"""
from __future__ import annotations

import ast
import json
import math
import re
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"

# Documents worth answering from. Deliberately a list rather than a walk
# of the repo: a walk picks up node_modules, run logs, and the PDK, and
# the cost of a corpus nobody chose is a retrieval layer that confidently
# cites a vendor README.
CORPUS_FILES = ("README.md", "soul.md")
CORPUS_GLOBS = ("docs/**/*.md", "references/*.md")

# Module docstrings, which is where most of this project's reasoning
# actually lives. Why gf180mcuA and B cannot run, why the surrogate
# dedupes on the library, why max_iterations_reached is not a review
# case — each is recorded where the decision was made rather than in a
# document, and a corpus of markdown alone answers "what is this" while
# being unable to answer "why is it like that".
#
# Prose only, never the code between it. Code is not an explanation, and
# indexing it ranks a passage by how often it happens to name a variable.
CORPUS_PY_GLOBS = ("pipeline/*.py",)
# Below this a docstring is a usage line, not an explanation, and adding
# it dilutes the corpus without adding an answer.
MIN_DOCSTRING = 200

# Standalone comment blocks are indexed too, and they are not a
# consolation prize for a thin docstring: this codebase writes its
# reasoning next to the decision. The measurement that ruled out
# gf180mcuA and B — neither ships an OpenRCX ruleset, so OpenLane quits
# during PDK load, found by spending 34 runs of a batch on it — is a
# comment block in collect.py and appears in no docstring and no
# markdown. There is as much of this (43KB across 70 blocks) as there is
# docstring, and skipping it means answering "why is it like that" from
# the design spec's general remarks instead of from the measurement.
MIN_COMMENT_BLOCK = 300
MIN_COMMENT_LINES = 4

# Words that appear in almost every question and carry no signal. Kept
# short on purpose — an aggressive stop list silently deletes real
# queries ("what is the power of ..." is about power).
STOPWORDS = frozenset("""
a an and are as at be but by can do does for from has have how i if in
into is it its me my not of on or should so than that the their them
then there these they this to us was what when where which who why will
with would you your
""".split())

# Letters in any script, not just ASCII. This was [a-z0-9_]+, which made
# every Korean question tokenize to nothing, score nothing, and land on
# the no-sources path — where the page told the reader, in Korean, that
# this repo cannot answer a question it answers well. That message is
# the one output this page treats as authoritative, and a regex was
# turning an honest refusal into a false one.
TOKEN = re.compile(r"[^\W]+", re.UNICODE)

# Korean for the words this corpus is written in. The corpus is English,
# so tokenizing Korean is necessary and not sufficient: the terms still
# have to reach English text, and nothing in "이 프로젝트는 무엇을 하나요"
# matches a document that says "project".
#
# Hand-written, like tool_retrieval.py's entries and for the same
# reason: it is small enough to read, every line is checkable, and a
# wrong answer is fixed by editing one line rather than retraining
# something. A test holds every target word to actually appearing in the
# corpus, so an entry cannot quietly point at a word that was renamed.
#
# It covers this project's vocabulary, not the Korean language. A
# question outside it falls back to whatever ASCII it contains — design
# names, metric keys and error codes are written the same way in both —
# and failing that, returns nothing, which stays the honest answer.
GLOSSARY = {
    "프로젝트": "project",
    "설계": "design",
    "면적": "area",
    "전력": "power",
    "타이밍": "timing",
    "슬랙": "slack",
    "공정": "pdk",
    "셀": "cell",
    "라이브러리": "library",
    "실행": "run",
    "후보": "candidate",
    "배치": "placement",
    "배선": "routing",
    "합성": "synthesis",
    "통과": "passed",
    "실패": "failed",
    "변종": "variant",
    "이용률": "utilization",
    "밀도": "density",
    "클럭": "clock",
    "주기": "period",
    "리포트": "report",
    "보고서": "report",
    "검증": "verification",
    "사인오프": "signoff",
    "에이전트": "agent",
    "파이프라인": "pipeline",
    "저장소": "store",
    "케이스": "case",
    "샘플": "sample",
    "목표": "target",
    "제약": "constraints",
    "복구": "repair",
    "리뷰": "review",
    "토폴로지": "topology",
    "다이": "die",
    "매크로": "macro",
    "네트리스트": "netlist",
    "시뮬레이션": "simulation",
    "모델": "model",
    "학습": "learning",
    "정확도": "accuracy",
}

# Below this, a passage shares only incidental vocabulary with the
# question. Returning it anyway is the failure that matters here: it
# hands a model a passage about something else and invites an answer
# anyway. Calibrated against the tests — an off-topic question scores
# under it, a real one well over.
MIN_SCORE = 0.25


def tokenize(text: str) -> list[str]:
    """Words, plus the parts of any underscore-joined identifier.

    This domain names things `report_timing`, `FP_CORE_UTIL`,
    `PL_TARGET_DENSITY_PCT`. Kept whole, `report_timing` matches nothing
    in a document whose headings say "timing"; split only, it stops being
    the precise key that makes an exact match worth ranking highly. Both
    are emitted, so the whole identifier scores where it appears and the
    parts still reach a section that discusses it in prose.
    """
    out: list[str] = []
    for word in TOKEN.findall(text.lower()):
        if word in STOPWORDS or len(word) < 2:
            continue
        out.append(word)
        if "_" in word:
            out.extend(p for p in word.split("_")
                       if len(p) > 1 and p not in STOPWORDS)
        # Korean is written without spaces between a noun and its
        # particle ("프로젝트는", "설계에서"), so a whole-word lookup
        # misses almost everything. Substring containment is crude and
        # right here: the glossary keys are domain nouns long enough not
        # to collide.
        for korean, english in GLOSSARY.items():
            if korean in word:
                out.extend(english.split())
    return out


def _split_sections(text: str, source: str) -> list[dict]:
    """One passage per markdown heading.

    Whole files are not passages. The layout-agent design spec is over
    100KB; returning it as "the answer" is the same as returning nothing,
    and it would crowd every other source out of a model's context.
    """
    out: list[dict] = []
    title = source
    body: list[str] = []

    def flush() -> None:
        joined = "\n".join(body).strip()
        if joined:
            out.append({"source": source, "title": title, "text": joined})

    for line in text.splitlines():
        if line.startswith("#"):
            flush()
            title = line.lstrip("#").strip() or source
            body = []
        else:
            body.append(line)
    flush()
    return out


def load_corpus(root: Path | str = REPO_ROOT) -> list[dict]:
    root = Path(root)
    paths: list[Path] = [root / name for name in CORPUS_FILES]
    for pattern in CORPUS_GLOBS:
        paths.extend(sorted(root.glob(pattern)))

    docs: list[dict] = []
    for path in paths:
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        docs.extend(_split_sections(text, path.relative_to(root).as_posix()))

    for pattern in CORPUS_PY_GLOBS:
        for path in sorted(root.glob(pattern)):
            # Not itself. This module explains the retrieval, not the
            # service, and its comments quote other files' reasoning as
            # examples — indexed, those quotations compete with the
            # files they were quoting and rank above them, because a
            # quotation is shorter and denser than what it quotes.
            if path.name == Path(__file__).name:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SyntaxError):
                # A module that will not parse is skipped rather than
                # indexed from its raw text: half a docstring and a
                # stack trace is not a passage.
                continue
            source = path.relative_to(root).as_posix()
            doc = ast.get_docstring(tree)
            if doc and len(doc) >= MIN_DOCSTRING:
                # The first line is the module's own one-sentence
                # statement of what it is, which is exactly what a
                # citation should say.
                docs.append({
                    "source": source,
                    "title": doc.strip().splitlines()[0].strip(),
                    "text": doc,
                })
            docs.extend(_comment_blocks(path, source))
    return docs


def _comment_blocks(path: Path, source: str) -> list[dict]:
    """Contiguous `#` prose, one passage per block.

    Read line-wise rather than from the AST, because Python discards
    comments — they are not in the tree at all. A `#` inside a string
    would be picked up wrongly by this, which is a real limitation and a
    cheap one: the cost is an occasional junk passage that scores badly,
    against the alternative of losing every recorded reason in the file.
    """
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out

    block: list[str] = []

    def flush() -> None:
        if len(block) < MIN_COMMENT_LINES:
            return
        text = " ".join(block).strip()
        if len(text) < MIN_COMMENT_BLOCK:
            return
        # The block's own first sentence names what it is about, the
        # same role a heading plays in a document.
        head = text.split(". ")[0]
        out.append({"source": source,
                    "title": head[:90] + ("…" if len(head) > 90 else ""),
                    "text": text})

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            block.append(stripped.lstrip("#").strip())
        else:
            flush()
            block = []
    flush()
    return out


def _idf(docs: list[dict]) -> dict[str, float]:
    seen: Counter[str] = Counter()
    for doc in docs:
        seen.update(set(tokenize(f"{doc['title']} {doc['text']}")))
    total = len(docs) or 1
    return {term: math.log(1 + total / count) for term, count in seen.items()}


def search(question: str, docs: list[dict], top: int = 4) -> list[dict]:
    """Passages that answer the question, best first, or nothing.

    Returns [] rather than the best of a bad set. "The nearest thing we
    have" is the wrong answer to a question this repo cannot answer, and
    it is worse than no answer because it reads like one.
    """
    terms = set(tokenize(question))
    if not terms:
        return []
    idf = _idf(docs)

    scored: list[dict] = []
    for doc in docs:
        # The path is part of what a passage is about: every section of
        # references/report-timing.md is about timing whether or not its
        # own heading says so, and without this a question naming the
        # format ranks the documents that merely mention it above the
        # one that defines it.
        haystack = f"{doc['source'].replace('/', ' ').replace('-', ' ')} " \
                   f"{doc['title']} {doc['text']}"
        words = tokenize(haystack)
        if not words:
            continue
        present = set(words)
        matched = sorted(terms & present)
        if not matched:
            continue
        # Weight of the question's terms this passage covers, over the
        # weight of everything the question asked for. A long passage
        # cannot win by being long: it has to cover rare terms.
        got = sum(idf.get(t, 0.0) for t in matched)
        want = sum(idf.get(t, 0.0) for t in terms) or 1.0
        # The title names what a section is about, so a match there is
        # worth more than one buried in prose.
        title_terms = terms & set(tokenize(
            f"{doc['source'].replace('/', ' ').replace('-', ' ')} {doc['title']}"))
        score = (got / want) + 0.35 * len(title_terms)
        scored.append({**doc, "score": round(score, 4), "matched": matched})

    scored.sort(key=lambda d: (-d["score"], d["source"]))
    return [d for d in scored if d["score"] >= MIN_SCORE][:top]


def store_facts(refdb: Path | str = REFDB) -> dict:
    """What the case store holds, counted now rather than remembered."""
    cases_dir = Path(refdb) / "cases"
    per_design: dict[str, dict] = {}
    cases = 0
    runs = 0

    for path in sorted(cases_dir.glob("*.json")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        cases += 1
        design = case.get("design") or path.name.split("__")[0]
        row = per_design.setdefault(design, {
            "design": design, "cases": 0, "candidate_runs": 0,
            "passed": 0, "technologies": set(), "closed_cases": 0,
        })
        row["cases"] += 1
        if case.get("winner_tag"):
            row["closed_cases"] += 1
        for iteration in case.get("iterations", []):
            for result in iteration.get("results", []):
                runs += 1
                row["candidate_runs"] += 1
                if (result.get("verdict") or {}).get("passed"):
                    row["passed"] += 1
                row["technologies"].add(result.get("scl") or "sky130_fd_sc_hd")

    designs = []
    for row in per_design.values():
        # A design with no passing candidate is reported at zero, not
        # dropped. cdc_twoclock has 104 real runs and no winner, and a
        # summary that omitted it would make the store look uniformly
        # successful — the one thing soul.md says this project will not
        # do.
        designs.append({**row, "technologies": sorted(row["technologies"])})
    designs.sort(key=lambda d: -d["candidate_runs"])
    return {"cases": cases, "candidate_runs": runs, "designs": designs}


def _facts_block(question: str, facts: dict) -> str | None:
    """The store's numbers, when the question is about results."""
    asked = set(tokenize(question))
    named = [d for d in facts["designs"] if d["design"].lower() in asked]
    # Questions about outcomes get the whole table; questions naming a
    # design get that design. A question about neither gets nothing,
    # rather than a table of numbers to pattern-match an answer out of.
    wants_results = asked & {
        "runs", "run", "passed", "pass", "results", "result", "candidates",
        "candidate", "samples", "designs", "recorded", "store", "many",
        "much", "closed", "failed", "area", "won", "winner",
    }
    rows = named or (facts["designs"] if wants_results else [])
    if not rows:
        return None

    lines = [
        f"reference-db holds {facts['cases']} cases and "
        f"{facts['candidate_runs']} real candidate runs."
    ]
    for row in rows:
        lines.append(
            f"- {row['design']}: {row['candidate_runs']} candidate runs, "
            f"{row['passed']} passed signoff, {row['cases']} cases "
            f"({row['closed_cases']} closed with a winner), "
            f"libraries: {', '.join(row['technologies'])}"
        )
    return "\n".join(lines)


INSTRUCTIONS = """\
You answer questions about the ppa-eda-agent project using ONLY the
sources below. Rules:

- Ground every claim in a source, and name the source file you used.
- If the sources do not answer the question, say so plainly. Do not
  fill the gap from general knowledge about EDA, OpenLane, or chip
  design — a confident answer this repo cannot support is the failure
  this retrieval exists to prevent.
- Never invent numbers. The figures under RECORDED RESULTS are computed
  from the live case store; use those and no others.
- Be concise and concrete. Prefer a short answer that cites a file over
  a long one that does not.
"""


def build_prompt(question: str, root: Path | str = REPO_ROOT,
                 refdb: Path | str = REFDB) -> dict:
    """The grounded prompt, plus the sources it was built from.

    Sources are returned whether or not a model is available — they are
    the half of the answer that needs no gateway key.
    """
    docs = load_corpus(root)
    hits = search(question, docs)
    facts = _facts_block(question, store_facts(refdb))

    sources = [
        {"source": h["source"], "title": h["title"],
         "score": h["score"], "matched": h["matched"],
         # Enough to read as an answer on its own when no model is
         # configured, bounded so a long section cannot crowd out the
         # others in a prompt.
         "excerpt": h["text"][:1200]}
        for h in hits
    ]

    if not sources and not facts:
        return {"sources": [], "facts": None, "prompt": None}

    parts = [INSTRUCTIONS, f"QUESTION: {question}", ""]
    if facts:
        parts += ["RECORDED RESULTS (live from reference-db):", facts, ""]
    for hit in sources:
        parts += [f"SOURCE {hit['source']} — {hit['title']}", hit["excerpt"], ""]
    return {"sources": sources, "facts": facts, "prompt": "\n".join(parts)}


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: service_qa.py <question>"}))
        raise SystemExit(2)
    print(json.dumps(build_prompt(" ".join(sys.argv[1:])), indent=2))


if __name__ == "__main__":
    main()
