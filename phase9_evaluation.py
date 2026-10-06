#!/usr/bin/env python3
"""Phase 9: Testing and evaluation toolkit for the AI file organizer."""

from __future__ import annotations

import argparse
import csv
import json
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from file_organizer import OrganizationDecision, organize_file


@dataclass(frozen=True)
class GroundTruthRecord:
    file_path: str
    expected_folder: str


@dataclass(frozen=True)
class EvaluationItem:
    file_path: str
    expected_folder: str
    predicted_folder: str
    correct: bool
    confidence: float
    base_category: str
    content_category: str
    needs_manual_review: bool
    review_reason: str
    action: str


def _normalize_folder(folder_path: str) -> str:
    parts = [part.strip() for part in folder_path.replace("\\", "/").split("/") if part.strip()]
    return "/".join(parts)


def _load_ground_truth(csv_path: str | Path) -> list[GroundTruthRecord]:
    path = Path(csv_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Ground truth CSV not found: {path}")

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        required = {"file_path", "expected_folder"}
        if not required.issubset(columns):
            raise ValueError(
                "Ground truth CSV must include columns: file_path, expected_folder"
            )

        rows: list[GroundTruthRecord] = []
        for raw in reader:
            file_path = (raw.get("file_path") or "").strip()
            expected_folder = (raw.get("expected_folder") or "").strip()
            if not file_path or not expected_folder:
                continue
            rows.append(
                GroundTruthRecord(
                    file_path=file_path,
                    expected_folder=_normalize_folder(expected_folder),
                )
            )
    if not rows:
        raise ValueError(f"No usable rows in ground truth CSV: {path}")
    return rows


def _load_manual_baseline_map(csv_path: str | Path) -> dict[str, str]:
    path = Path(csv_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Manual baseline CSV not found: {path}")

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        required = {"file_path", "manual_folder"}
        if not required.issubset(columns):
            raise ValueError("Manual baseline CSV must include columns: file_path, manual_folder")

        mapping: dict[str, str] = {}
        for raw in reader:
            file_path = (raw.get("file_path") or "").strip()
            manual_folder = (raw.get("manual_folder") or "").strip()
            if not file_path or not manual_folder:
                continue
            mapping[file_path] = _normalize_folder(manual_folder)
    if not mapping:
        raise ValueError(f"No usable manual baseline rows in CSV: {path}")
    return mapping


def _evaluate_records(
    dataset_root: str | Path,
    records: list[GroundTruthRecord],
) -> tuple[list[EvaluationItem], float]:
    root = Path(dataset_root).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Dataset root not found: {root}")
    if not root.is_dir():
        raise ValueError(f"Dataset root is not a directory: {root}")

    items: list[EvaluationItem] = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="organizer_eval_") as tmp_destination:
        for record in records:
            source_path = (root / record.file_path).resolve()
            if not source_path.exists() or not source_path.is_file():
                raise FileNotFoundError(
                    f"Ground truth file missing: {source_path} (from {record.file_path})"
                )

            decision: OrganizationDecision = organize_file(
                file_path=source_path,
                destination_root=tmp_destination,
                move_files=False,
                dry_run=True,
            )
            predicted_folder = _normalize_folder(decision.folder_path)
            correct = predicted_folder == record.expected_folder

            items.append(
                EvaluationItem(
                    file_path=record.file_path,
                    expected_folder=record.expected_folder,
                    predicted_folder=predicted_folder,
                    correct=correct,
                    confidence=decision.confidence,
                    base_category=decision.base_category,
                    content_category=decision.content_category,
                    needs_manual_review=decision.needs_manual_review,
                    review_reason=decision.review_reason,
                    action=decision.action,
                )
            )

    elapsed_seconds = time.perf_counter() - started
    return items, elapsed_seconds


def _build_confusion(items: list[EvaluationItem]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {}
    for item in items:
        expected = item.expected_folder
        predicted = item.predicted_folder
        row = matrix.setdefault(expected, {})
        row[predicted] = row.get(predicted, 0) + 1
    return matrix


def _top_errors(items: list[EvaluationItem], limit: int = 10) -> list[dict[str, Any]]:
    errors = [item for item in items if not item.correct]
    errors.sort(key=lambda item: item.confidence, reverse=True)
    payload: list[dict[str, Any]] = []
    for item in errors[:limit]:
        payload.append(
            {
                "file_path": item.file_path,
                "expected_folder": item.expected_folder,
                "predicted_folder": item.predicted_folder,
                "confidence": round(item.confidence, 2),
                "content_category": item.content_category,
                "needs_manual_review": item.needs_manual_review,
                "review_reason": item.review_reason,
            }
        )
    return payload


def _summary_metrics(
    items: list[EvaluationItem],
    ai_runtime_seconds: float,
    manual_seconds_per_file: float,
    manual_error_rate: float,
    manual_baseline_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    total = len(items)
    correct = sum(1 for item in items if item.correct)
    incorrect = total - correct
    accuracy = (correct / total) if total else 0.0

    needs_review = [item for item in items if item.needs_manual_review]
    review_count = len(needs_review)
    review_correct = sum(1 for item in needs_review if item.correct)
    review_accuracy = (review_correct / review_count) if review_count else 0.0

    estimated_manual_seconds = manual_seconds_per_file * total
    time_saved_seconds = estimated_manual_seconds - ai_runtime_seconds
    time_saved_percent = (
        (time_saved_seconds / estimated_manual_seconds) * 100
        if estimated_manual_seconds > 0
        else 0.0
    )
    speedup = estimated_manual_seconds / ai_runtime_seconds if ai_runtime_seconds > 0 else float("inf")

    manual_baseline_source = "assumed_error_rate"
    if manual_baseline_map:
        manual_correct = 0
        for item in items:
            manual_folder = manual_baseline_map.get(item.file_path)
            if manual_folder is not None and manual_folder == item.expected_folder:
                manual_correct += 1
        manual_accuracy = manual_correct / total if total else 0.0
        manual_mistakes = total - manual_correct
        manual_baseline_source = "manual_baseline_csv"
    else:
        manual_accuracy = 1.0 - manual_error_rate
        manual_mistakes = total * manual_error_rate

    ai_mistakes = incorrect
    mistakes_reduced = manual_mistakes - ai_mistakes
    mistakes_reduced_percent = (
        (mistakes_reduced / manual_mistakes) * 100 if manual_mistakes > 0 else 0.0
    )

    return {
        "total_files": total,
        "accuracy": round(accuracy, 4),
        "correct": correct,
        "incorrect": incorrect,
        "needs_manual_review_count": review_count,
        "needs_manual_review_rate": round((review_count / total) if total else 0.0, 4),
        "review_bucket_accuracy": round(review_accuracy, 4),
        "ai_runtime_seconds": round(ai_runtime_seconds, 4),
        "ai_avg_seconds_per_file": round((ai_runtime_seconds / total) if total else 0.0, 4),
        "estimated_manual_seconds": round(estimated_manual_seconds, 4),
        "estimated_manual_avg_seconds_per_file": round(manual_seconds_per_file, 4),
        "estimated_time_saved_seconds": round(time_saved_seconds, 4),
        "estimated_time_saved_percent": round(time_saved_percent, 2),
        "estimated_speedup_factor": round(speedup, 2) if speedup != float("inf") else "inf",
        "manual_baseline_source": manual_baseline_source,
        "manual_baseline_accuracy": round(manual_accuracy, 4),
        "manual_mistakes": round(manual_mistakes, 4),
        "ai_mistakes": ai_mistakes,
        "mistakes_reduced": round(mistakes_reduced, 4),
        "mistakes_reduced_percent": round(mistakes_reduced_percent, 2),
    }


def evaluate_dataset(
    dataset_root: str | Path,
    ground_truth_csv: str | Path,
    manual_seconds_per_file: float = 20.0,
    manual_error_rate: float = 0.12,
    manual_baseline_csv: str | Path | None = None,
) -> dict[str, Any]:
    records = _load_ground_truth(ground_truth_csv)
    manual_baseline_map = (
        _load_manual_baseline_map(manual_baseline_csv) if manual_baseline_csv else None
    )
    items, elapsed = _evaluate_records(dataset_root=dataset_root, records=records)

    payload = {
        "dataset_root": str(Path(dataset_root).expanduser().resolve()),
        "ground_truth_csv": str(Path(ground_truth_csv).expanduser().resolve()),
        "manual_baseline_csv": (
            str(Path(manual_baseline_csv).expanduser().resolve())
            if manual_baseline_csv
            else None
        ),
        "summary": _summary_metrics(
            items=items,
            ai_runtime_seconds=elapsed,
            manual_seconds_per_file=manual_seconds_per_file,
            manual_error_rate=manual_error_rate,
            manual_baseline_map=manual_baseline_map,
        ),
        "confusion_matrix": _build_confusion(items),
        "top_errors": _top_errors(items, limit=10),
        "files": [asdict(item) for item in items],
    }
    return payload


def _render_markdown_report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines: list[str] = []
    lines.append("# Phase 9 Evaluation Report")
    lines.append("")
    lines.append(f"- Dataset Root: `{result['dataset_root']}`")
    lines.append(f"- Ground Truth CSV: `{result['ground_truth_csv']}`")
    if result.get("manual_baseline_csv"):
        lines.append(f"- Manual Baseline CSV: `{result['manual_baseline_csv']}`")
    lines.append("")
    lines.append("## Outcome")
    lines.append("")
    lines.append(f"- Classification Accuracy: **{summary['accuracy'] * 100:.2f}%**")
    lines.append(f"- Correct / Total: **{summary['correct']} / {summary['total_files']}**")
    lines.append(f"- Files Sent to Manual Review: **{summary['needs_manual_review_count']}**")
    lines.append(f"- AI Runtime: **{summary['ai_runtime_seconds']} seconds**")
    lines.append(
        f"- Estimated Time Saved vs Manual: **{summary['estimated_time_saved_seconds']} seconds "
        f"({summary['estimated_time_saved_percent']}%)**"
    )
    lines.append(
        f"- Human Mistakes Reduced: **{summary['mistakes_reduced']} "
        f"({summary['mistakes_reduced_percent']}%)**"
    )
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append(
        f"- Manual baseline source: `{summary['manual_baseline_source']}` "
        f"(accuracy: `{summary['manual_baseline_accuracy']}`)"
    )
    lines.append(
        "- Time-saved and mistake-reduction estimates depend on the manual baseline settings."
    )
    lines.append("")
    lines.append("## Top Misclassifications")
    lines.append("")
    top_errors = result.get("top_errors", [])
    if not top_errors:
        lines.append("- No misclassifications found.")
    else:
        for error in top_errors:
            lines.append(
                f"- `{error['file_path']}` expected `{error['expected_folder']}`, "
                f"predicted `{error['predicted_folder']}` (confidence {error['confidence']})"
            )
    lines.append("")
    return "\n".join(lines)


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate organizer accuracy, speed, and reliability on a labeled dataset."
    )
    parser.add_argument("dataset_root", help="Root directory containing the test files.")
    parser.add_argument(
        "ground_truth_csv",
        help="CSV with file_path and expected_folder columns.",
    )
    parser.add_argument(
        "--manual-baseline-csv",
        help="Optional CSV with file_path and manual_folder for measured manual baseline.",
    )
    parser.add_argument(
        "--manual-seconds-per-file",
        type=float,
        default=20.0,
        help="Assumed manual sorting time per file when manual baseline timing is unavailable.",
    )
    parser.add_argument(
        "--manual-error-rate",
        type=float,
        default=0.12,
        help="Assumed manual error rate (0-1) if no manual baseline CSV is provided.",
    )
    parser.add_argument(
        "--output-json",
        help="Optional path to write full JSON evaluation output.",
    )
    parser.add_argument(
        "--output-report",
        help="Optional path to write a markdown summary report.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output to stdout.",
    )
    return parser


def main() -> int:
    parser = _build_cli()
    args = parser.parse_args()

    if args.manual_error_rate < 0 or args.manual_error_rate > 1:
        parser.error("--manual-error-rate must be between 0 and 1.")
    if args.manual_seconds_per_file <= 0:
        parser.error("--manual-seconds-per-file must be greater than 0.")

    result = evaluate_dataset(
        dataset_root=args.dataset_root,
        ground_truth_csv=args.ground_truth_csv,
        manual_seconds_per_file=args.manual_seconds_per_file,
        manual_error_rate=args.manual_error_rate,
        manual_baseline_csv=args.manual_baseline_csv,
    )

    indent = 2 if args.pretty else None
    print(json.dumps(result, indent=indent))

    if args.output_json:
        output_json = Path(args.output_json).expanduser().resolve()
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(result, indent=2), encoding="utf-8")

    if args.output_report:
        report = _render_markdown_report(result)
        output_report = Path(args.output_report).expanduser().resolve()
        output_report.parent.mkdir(parents=True, exist_ok=True)
        output_report.write_text(report, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
