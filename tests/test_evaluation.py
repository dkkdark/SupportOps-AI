import json
from pathlib import Path


def test_evaluation_has_disjoint_fixed_sets():
    data = json.loads(Path("eval/dataset.json").read_text())
    assert len(data["cases"]) == 100
    ids = [c["id"] for c in data["cases"]]
    assert len(set(ids)) == 100
    dev = {c["description"] for c in data["cases"] if c["split"] == "dev"}
    holdout = {c["description"] for c in data["cases"] if c["split"] == "holdout"}
    assert not dev.intersection(holdout)
    assert len([c for c in data["cases"] if c["split"] == "holdout"]) == 20
