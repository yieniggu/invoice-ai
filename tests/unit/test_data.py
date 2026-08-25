import csv
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from invoiceops.ml.data import generate_synthetic_dataset
from invoiceops.ml.features import FEATURE_SCHEMA_VERSION, MODEL_FEATURES


def _read_split(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as file:
        return list(csv.DictReader(file))


def test_dataset_generation_has_contract_ranges_and_reasonable_target_rate(tmp_path: Path) -> None:
    output_dir = generate_synthetic_dataset(
        seed=20260826,
        rows=1_000,
        version="invoice-risk-v1",
        output_root=tmp_path / "data",
    )

    splits = [
        _read_split(output_dir / filename)
        for filename in ("train.csv", "validation.csv", "test.csv")
    ]
    rows = [row for split in splits for row in split]

    assert set(output_dir.iterdir()) == {
        output_dir / "train.csv",
        output_dir / "validation.csv",
        output_dir / "test.csv",
        output_dir / "metadata.json",
    }
    assert list(rows[0]) == [
        "invoice_id",
        "submitted_at",
        *MODEL_FEATURES,
        "manual_review_required",
    ]
    assert all(0 <= int(row["vendor_tenure_days"]) <= 3_650 for row in rows)
    assert {row["country_risk"] for row in rows} <= {"low", "medium", "high"}
    assert {row["has_purchase_order"] for row in rows} <= {"True", "False"}
    assert {row["three_way_match"] for row in rows} <= {"True", "False"}
    assert {row["bank_account_recently_changed"] for row in rows} <= {"True", "False"}
    assert all(int(row["invoice_amount_cents"]) > 0 for row in rows)
    assert 0.8 <= sum(float(row["amount_vs_vendor_median"]) for row in rows) / len(rows) <= 1.2

    positive_rate = sum(row["manual_review_required"] == "True" for row in rows) / len(rows)
    assert 0.15 <= positive_rate <= 0.25
    assert positive_rate != 0.20


def test_dataset_generation_is_chronological_and_has_non_overlapping_ids(tmp_path: Path) -> None:
    output_dir = generate_synthetic_dataset(
        seed=91,
        rows=101,
        version="invoice-risk-v1",
        output_root=tmp_path / "data",
    )
    train, validation, test = (
        _read_split(output_dir / filename)
        for filename in ("train.csv", "validation.csv", "test.csv")
    )

    assert [len(split) for split in (train, validation, test)] == [70, 15, 16]
    assert len({row["invoice_id"] for split in (train, validation, test) for row in split}) == 101
    assert train[-1]["submitted_at"] < validation[0]["submitted_at"] < test[0]["submitted_at"]


def test_dataset_generation_12000_rows_has_required_split_sizes_and_target_rate(
    tmp_path: Path,
) -> None:
    output_dir = generate_synthetic_dataset(
        seed=20260826,
        rows=12_000,
        version="invoice-risk-v1",
        output_root=tmp_path / "data",
    )
    splits = {
        filename: _read_split(output_dir / filename)
        for filename in ("train.csv", "validation.csv", "test.csv")
    }
    rows = [row for split in splits.values() for row in split]
    metadata = json.loads((output_dir / "metadata.json").read_text())

    assert {filename: len(split) for filename, split in splits.items()} == {
        "train.csv": 8_400,
        "validation.csv": 1_800,
        "test.csv": 1_800,
    }
    assert 0.15 <= sum(row["manual_review_required"] == "True" for row in rows) / len(rows) <= 0.25
    assert metadata["rows"] == 12_000
    assert metadata["train_rows"] == 8_400
    assert metadata["validation_rows"] == 1_800
    assert metadata["test_rows"] == 1_800


def test_dataset_generation_is_reproducible_and_metadata_hashes_match(tmp_path: Path) -> None:
    first = generate_synthetic_dataset(
        seed=42,
        rows=100,
        version="invoice-risk-v1",
        output_root=tmp_path / "first",
    )
    second = generate_synthetic_dataset(
        seed=42,
        rows=100,
        version="invoice-risk-v1",
        output_root=tmp_path / "second",
    )

    for filename in ("train.csv", "validation.csv", "test.csv", "metadata.json"):
        assert (first / filename).read_bytes() == (second / filename).read_bytes()

    metadata = json.loads((first / "metadata.json").read_text())
    assert metadata == {
        "dataset_version": "invoice-risk-v1",
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "rows": 100,
        "seed": 42,
        "split_sha256": {
            filename: hashlib.sha256((first / filename).read_bytes()).hexdigest()
            for filename in ("train.csv", "validation.csv", "test.csv")
        },
        "target": "manual_review_required",
        "test_rows": 15,
        "train_rows": 70,
        "validation_rows": 15,
    }
    assert all(
        not Path(value).is_absolute() for value in metadata.values() if isinstance(value, str)
    )


def test_cli_writes_only_the_versioned_dataset_directory(tmp_path: Path) -> None:
    script = Path(__file__).parents[2] / "scripts" / "generate_synthetic_dataset.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--seed",
            "7",
            "--rows",
            "10",
            "--version",
            "invoice-risk-v1",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "data/invoice-risk-v1"
    assert {
        path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*") if path.is_file()
    } == {
        "data/invoice-risk-v1/train.csv",
        "data/invoice-risk-v1/validation.csv",
        "data/invoice-risk-v1/test.csv",
        "data/invoice-risk-v1/metadata.json",
    }


def test_cli_rejects_row_counts_that_leave_a_split_empty(tmp_path: Path) -> None:
    script = Path(__file__).parents[2] / "scripts" / "generate_synthetic_dataset.py"

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--seed",
            "7",
            "--rows",
            "6",
            "--version",
            "invoice-risk-v1",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert result.stdout == ""
    assert "error: rows must be an integer of at least 7" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("version", ["../outside", "/absolute", "nested/version", ""])
def test_dataset_generation_rejects_unsafe_versions(tmp_path: Path, version: str) -> None:
    with pytest.raises(ValueError, match="version"):
        generate_synthetic_dataset(seed=1, rows=10, version=version, output_root=tmp_path)


@pytest.mark.parametrize("rows", [0, -1, True, 6])
def test_dataset_generation_rejects_invalid_row_counts(tmp_path: Path, rows: int) -> None:
    with pytest.raises(ValueError, match="rows"):
        generate_synthetic_dataset(
            seed=1,
            rows=rows,
            version="invoice-risk-v1",
            output_root=tmp_path,
        )
