"""Frozen trial entrypoint: replay each earlier response exactly once.

The unchanged task and current measured rows already appear in each capsule.
Reinserting entire earlier capsules would recursively duplicate their histories
and unfairly inflate the full_history arm's input cost. Raw original prompts
remain in trials/<id>/prompt.txt for the audit.
"""
from pathlib import Path
import sys

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "pipeline/context_search_benchmark.py").is_file())
sys.path.insert(0, str(ROOT / "pipeline"))
from context_search_benchmark import Experiment, read, write
from review_context import file_hash


class TrialDriver(Experiment):
    def prompt(self, policy, rows, seeds, previous, round_number):
        dialogue = [{"round": t["round"], "response": t["response"]} for t in previous]
        return super().prompt(policy, rows, seeds, dialogue, round_number)


if __name__ == "__main__":
    experiment = Path(__file__).resolve().parent
    record = experiment / "search-driver.json"
    identity = {"driver_sha256": file_hash(__file__),
                "upstream_runner_sha256": file_hash(ROOT / "pipeline/context_search_benchmark.py"),
                "history_replay": "Same task and current measured rows once, plus each prior model response once. No recursive prompt duplication.",
                "status": "fixed_before_first_model_trial"}
    if record.exists():
        if read(record) != identity:
            raise ValueError("search driver changed during experiment")
    else:
        write(record, identity)
    TrialDriver(experiment).search()
