import argparse
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import review_context as context
import review_context_eval as evaluator


@pytest.fixture
def inputs(tmp_path):
    old = {"design": "demo", "diagnosis": "OLD_SECRET", "iterations": [{"results": [
        {"tag": "old", "overrides": {"FP_CORE_UTIL": 40}, "verdict": {"passed": False}}]}]}
    case = {"design": "demo", "diagnosis": "CURRENT_SECRET", "iterations": [{"results": [
        {"tag": "seed", "overrides": {"FP_CORE_UTIL": 35}, "verdict": {"passed": False}},
        {"tag": "deferred", "overrides": {}, "not_evaluated": True}]}]}
    for name, value in [("demo__1.json", old), ("demo__2.json", case), ("config.json", {"CLOCK_PERIOD": 10})]:
        (tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
    return argparse.Namespace(case_file=tmp_path / "demo__2.json", design_config=tmp_path / "config.json",
        seed_tag="seed", allow_override=["FP_CORE_UTIL"], history_limit=12,
        repetitions=2, candidate_limit=3, random_seed=42, output_dir=tmp_path / "experiment")


def frozen(args):
    return context.snapshot(args.case_file, args.design_config, args.seed_tag,
                            args.allow_override, args.history_limit)


def test_visibility_and_unchanged_inputs(inputs):
    before = inputs.case_file.read_bytes()
    snap = frozen(inputs)
    full = context.capsule(snap, "full_history")
    fresh = context.capsule(snap, "fresh_evidence")
    seed = context.capsule(snap, "seed_only")
    assert "OLD_SECRET" in context.canonical(full)
    assert "SECRET" not in context.canonical(fresh)
    assert full["current"] == fresh["current"]
    assert full["history"] == fresh["history"]
    assert seed["history"] == []
    assert [r["tag"] for r in context.results(seed["current"])] == ["seed"]
    assert inputs.case_file.read_bytes() == before


def test_reject_unmeasured_and_protected(inputs):
    inputs.seed_tag = "deferred"
    with pytest.raises(ValueError, match="unmeasured"):
        frozen(inputs)
    for key in ["CLOCK_PERIOD", "RUN_LVS", "SIGNOFF_SDC_FILE", "STA_CORNERS"]:
        with pytest.raises(ValueError):
            context.validate_allowed([key])


def test_hidden_duplicate_and_visible_citations(inputs):
    proposal = {"diagnosis": "test", "candidates": [{"tag": "next", "overrides": {"FP_CORE_UTIL": 40},
                "hypothesis": "test utilization", "evidence_tags": ["old"]}]}
    score = evaluator.score_response(frozen(inputs), "seed_only", proposal, 3)
    row = score["candidates"][0]
    assert row["prior_config_signature_duplicate"]
    assert row["errors"] == ["evidence tags must cite visible candidates"]
    assert score["ppa_improvement"] is None
    proposal["candidates"][0]["overrides"] = {"CLOCK_PERIOD": 20}
    assert "override outside allowed tuning keys" in evaluator.score_response(
        frozen(inputs), "full_history", proposal, 3)["candidates"][0]["errors"]


def test_prepare_score_integrity_and_missing(inputs, tmp_path):
    manifest = evaluator.prepare(inputs)
    assert len(manifest["trials"]) == 6
    assert all(sum(t["policy"] == p for t in manifest["trials"]) == 2 for p in context.POLICIES)
    with pytest.raises(FileExistsError):
        evaluator.prepare(inputs)
    responses = tmp_path / "responses"
    responses.mkdir()
    (responses / (manifest["trials"][0]["id"] + ".json")).write_text("not json")
    args = argparse.Namespace(experiment=inputs.output_dir, responses=responses, output=tmp_path / "score.json")
    report = evaluator.score(args)
    assert report["trials"][0]["status"] == "invalid_response"
    assert report["trials"][1]["status"] == "missing"
    assert report["cost"] is None
    path = inputs.output_dir / manifest["trials"][0]["prompt"]
    path.write_text("tampered")
    with pytest.raises(ValueError, match="integrity"):
        evaluator.score(args)


def test_request_opt_in(inputs, tmp_path):
    import request_review
    before = inputs.case_file.read_bytes()
    inputs.design = "demo"
    inputs.context_policy = "fresh_evidence"
    inputs.output_dir = tmp_path / "requests"
    request_review.cmd_request(inputs)
    requests = list(inputs.output_dir.glob("*__request.md"))
    assert len(requests) == 1
    text = requests[0].read_text(encoding="utf-8")
    assert "SECRET" not in text
    assert '"old"' in text
    assert inputs.case_file.read_bytes() == before
