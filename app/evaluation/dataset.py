"""Load versioned evaluation datasets."""

from __future__ import annotations

import json
from pathlib import Path

from app.evaluation.models import CaseSplit, EvalCase, EvalDataset

_DATA_DIR = Path(__file__).with_name("data")
_DEFAULT = _DATA_DIR / "dataset_v1.json"


def dataset_path(version: str = "v1") -> Path:
    path = _DATA_DIR / f"dataset_{version}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Unknown dataset version '{version}': {path}")
    return path


def load_dataset(version: str = "v1") -> EvalDataset:
    raw = json.loads(dataset_path(version).read_text(encoding="utf-8"))
    return EvalDataset.model_validate(raw)


def cases_for_split(
    dataset: EvalDataset,
    *,
    include_held_out: bool = True,
) -> list[EvalCase]:
    if include_held_out:
        return list(dataset.cases)
    return [c for c in dataset.cases if c.split is CaseSplit.DEV]


def held_out_ids(dataset: EvalDataset) -> list[str]:
    return [c.id for c in dataset.cases if c.split is CaseSplit.HELD_OUT]
