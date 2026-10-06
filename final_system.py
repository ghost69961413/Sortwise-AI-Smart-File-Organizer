#!/usr/bin/env python3
"""Phase 10: Unified entrypoint for the AI file organizer project."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from dataclasses import asdict
from pathlib import Path
from typing import Any

from content_analyzer import analyze_directory_content, analyze_file_content
from file_organizer import organize_directory, organize_file
from file_type_detector import detect_file_type, scan_directory
from phase9_evaluation import evaluate_dataset


def _json_print(payload: Any, pretty: bool) -> None:
    print(json.dumps(payload, indent=2 if pretty else None))


def _write_json(path_value: str | Path, payload: Any) -> None:
    output_path = Path(path_value).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _safe_restore_path(target: Path) -> Path:
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = target.with_name(f"{stem}_undo{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def _load_undo_items(log_payload: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(log_payload.get("files"), list):
        return [item for item in log_payload["files"] if isinstance(item, dict)]
    if isinstance(log_payload.get("decisions"), list):
        return [item for item in log_payload["decisions"] if isinstance(item, dict)]
    if isinstance(log_payload.get("file"), dict):
        return [log_payload["file"]]
    raise ValueError("Log does not contain 'files' or 'decisions' entries.")


def run_undo(args: argparse.Namespace) -> int:
    log_path = Path(args.log_file).expanduser().resolve()
    if not log_path.exists():
        raise FileNotFoundError(f"Undo log file not found: {log_path}")

    payload = json.loads(log_path.read_text(encoding="utf-8"))
    items = _load_undo_items(payload)

    results: list[dict[str, Any]] = []
    for item in items:
        action = str(item.get("action", "")).strip().lower()
        source_path = Path(str(item.get("source_path", "")).strip()).expanduser()
        destination_path = Path(str(item.get("destination_path", "")).strip()).expanduser()

        result: dict[str, Any] = {
            "source_path": str(source_path),
            "destination_path": str(destination_path),
            "original_action": action,
            "undo_action": "skipped",
            "status": "skipped",
            "reason": "",
        }

        # Undo for copied files: remove the copied destination file.
        if action in {"copied", "copied_override"}:
            if destination_path.exists() and destination_path.is_file():
                if args.dry_run:
                    result["undo_action"] = "would_delete_copy"
                    result["status"] = "planned"
                    result["reason"] = "Dry run; destination copy would be deleted."
                else:
                    destination_path.unlink()
                    result["undo_action"] = "deleted_copy"
                    result["status"] = "done"
                    result["reason"] = "Copied file removed."
            else:
                result["undo_action"] = "delete_copy"
                result["status"] = "skipped"
                result["reason"] = "Destination copy not found."

        # Undo for moved files: move destination file back to original source.
        elif action in {"moved", "moved_override"}:
            if destination_path.exists() and destination_path.is_file():
                restore_target = _safe_restore_path(source_path)
                if args.dry_run:
                    result["undo_action"] = "would_restore_move"
                    result["status"] = "planned"
                    result["restore_target"] = str(restore_target)
                    result["reason"] = "Dry run; moved file would be restored."
                else:
                    restore_target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(destination_path), str(restore_target))
                    result["undo_action"] = "restored_move"
                    result["status"] = "done"
                    result["restore_target"] = str(restore_target)
                    result["reason"] = "Moved file restored to source path."
            else:
                result["undo_action"] = "restore_move"
                result["status"] = "skipped"
                result["reason"] = "Moved destination file not found."

        else:
            result["reason"] = "Unsupported/non-final action in log; nothing to undo."

        results.append(result)

    summary: dict[str, int] = {}
    for item in results:
        key = item["undo_action"]
        summary[key] = summary.get(key, 0) + 1

    undo_payload = {
        "log_file": str(log_path),
        "dry_run": bool(args.dry_run),
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_items": len(results),
            "undo_actions": summary,
            "done_count": sum(1 for item in results if item["status"] == "done"),
            "planned_count": sum(1 for item in results if item["status"] == "planned"),
            "skipped_count": sum(1 for item in results if item["status"] == "skipped"),
        },
        "files": results,
    }

    if args.output_json:
        _write_json(args.output_json, undo_payload)

    _json_print(undo_payload, pretty=args.pretty)
    return 0


def run_detect(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser().resolve()
    if target.is_file():
        _json_print(asdict(detect_file_type(target)), pretty=args.pretty)
        return 0
    if target.is_dir():
        results = scan_directory(target, recursive=not args.non_recursive)
        payload = {
            "summary": {
                "total_files": len(results),
            },
            "files": [asdict(item) for item in results],
        }
        _json_print(payload, pretty=args.pretty)
        return 0
    raise FileNotFoundError(f"Path not found: {target}")


def run_analyze(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser().resolve()
    if target.is_file():
        _json_print(asdict(analyze_file_content(target)), pretty=args.pretty)
        return 0
    if target.is_dir():
        results = analyze_directory_content(target, recursive=not args.non_recursive)
        payload = {
            "summary": {
                "total_files": len(results),
            },
            "files": [asdict(item) for item in results],
        }
        _json_print(payload, pretty=args.pretty)
        return 0
    raise FileNotFoundError(f"Path not found: {target}")


def run_organize(args: argparse.Namespace) -> int:
    source = Path(args.source).expanduser().resolve()
    destination = Path(args.destination).expanduser().resolve()

    if source.is_file():
        decision = organize_file(
            file_path=source,
            destination_root=destination,
            move_files=args.move,
            dry_run=args.dry_run,
        )
        payload = {
            "run_type": "organize_file",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "source": str(source),
            "destination_root": str(destination),
            "move_mode": bool(args.move),
            "dry_run": bool(args.dry_run),
            "file": asdict(decision),
        }
        if args.log_file:
            _write_json(args.log_file, payload)
        _json_print(payload, pretty=args.pretty)
        return 0

    if source.is_dir():
        decisions = organize_directory(
            source_directory=source,
            destination_root=destination,
            recursive=not args.non_recursive,
            move_files=args.move,
            dry_run=args.dry_run,
        )
        payload = {
            "run_type": "organize_directory",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "source": str(source),
            "destination_root": str(destination),
            "move_mode": bool(args.move),
            "dry_run": bool(args.dry_run),
            "summary": {
                "total_files": len(decisions),
                "actions": {
                    action: sum(1 for d in decisions if d.action == action)
                    for action in sorted({d.action for d in decisions})
                },
                "needs_manual_review": sum(1 for d in decisions if d.needs_manual_review),
            },
            "files": [asdict(item) for item in decisions],
        }
        if args.log_file:
            _write_json(args.log_file, payload)
        _json_print(payload, pretty=args.pretty)
        return 0

    raise FileNotFoundError(f"Source path not found: {source}")


def run_evaluate(args: argparse.Namespace) -> int:
    payload = evaluate_dataset(
        dataset_root=args.dataset_root,
        ground_truth_csv=args.ground_truth_csv,
        manual_seconds_per_file=args.manual_seconds_per_file,
        manual_error_rate=args.manual_error_rate,
        manual_baseline_csv=args.manual_baseline_csv,
    )
    _json_print(payload, pretty=args.pretty)
    return 0


def run_ui(_args: argparse.Namespace) -> int:
    try:
        from organizer_ui import main as launch_ui
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "UI mode requires Tkinter. This Python runtime does not include `_tkinter`."
        ) from error
    return launch_ui()


def _make_demo_input(input_dir: Path) -> None:
    input_dir.mkdir(parents=True, exist_ok=True)

    (input_dir / "mountain_trip.png").write_bytes(b"\x89PNG\r\n\x1a\nabc")
    (input_dir / "family_photo.jpg").write_bytes(b"\xff\xd8\xff\xe0abcd")
    (input_dir / "birthday_party.jpg").write_bytes(b"\xff\xd8\xff\xe0abcd")
    (input_dir / "april_invoice.pdf").write_text(
        "%PDF-1.4 invoice payment receipt amount due", encoding="utf-8"
    )
    (input_dir / "q2_report.pdf").write_text(
        "%PDF-1.4 project report proposal roadmap contract", encoding="utf-8"
    )
    (input_dir / "lecture_notes.pdf").write_text(
        "%PDF-1.4 lecture notes course syllabus", encoding="utf-8"
    )
    (input_dir / "my_resume.txt").write_text("my resume and profile details", encoding="utf-8")
    (input_dir / "db_assignment.txt").write_text(
        "assignment thesis course university homework", encoding="utf-8"
    )
    (input_dir / "recommendation_letter.txt").write_text(
        "recommendation letter for internship", encoding="utf-8"
    )
    (input_dir / "random_blob.bin").write_bytes(b"\x00\x01\x02\x03\x04")
    (input_dir / "zoom_meeting_recording.mp4").write_text("xxxxftypisomxxxx", encoding="utf-8")


def run_demo(args: argparse.Namespace) -> int:
    root = Path(args.demo_root).expanduser().resolve()
    input_dir = root / "input_mixed_files"
    output_dir = root / "output_organized_files"

    if root.exists() and args.clean:
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    input_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.generate_sample:
        _make_demo_input(input_dir)

    decisions = organize_directory(
        source_directory=input_dir,
        destination_root=output_dir,
        recursive=True,
        move_files=False,
        dry_run=False,
    )

    folder_snapshot = sorted(
        str(path.relative_to(output_dir))
        for path in output_dir.rglob("*")
        if path.is_file()
    )
    payload = {
        "demo_root": str(root),
        "input_folder": str(input_dir),
        "output_folder": str(output_dir),
        "processed_files": len(decisions),
        "needs_manual_review": sum(1 for d in decisions if d.needs_manual_review),
        "decisions": [asdict(item) for item in decisions],
        "output_snapshot": folder_snapshot,
    }

    if args.output_json:
        _write_json(args.output_json, payload)

    _json_print(payload, pretty=args.pretty)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Final AI file organizer system (Phase 10)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    detect_parser = subparsers.add_parser("detect", help="Run Phase 4 file-type detection.")
    detect_parser.add_argument("path", help="File or directory path.")
    detect_parser.add_argument("--non-recursive", action="store_true")
    detect_parser.add_argument("--pretty", action="store_true")
    detect_parser.set_defaults(func=run_detect)

    analyze_parser = subparsers.add_parser("analyze", help="Run Phase 5 content analysis.")
    analyze_parser.add_argument("path", help="File or directory path.")
    analyze_parser.add_argument("--non-recursive", action="store_true")
    analyze_parser.add_argument("--pretty", action="store_true")
    analyze_parser.set_defaults(func=run_analyze)

    organize_parser = subparsers.add_parser("organize", help="Run Phase 6+ organization.")
    organize_parser.add_argument("source", help="Source file/folder.")
    organize_parser.add_argument("destination", help="Destination root folder.")
    organize_parser.add_argument("--move", action="store_true")
    organize_parser.add_argument("--dry-run", action="store_true")
    organize_parser.add_argument("--non-recursive", action="store_true")
    organize_parser.add_argument("--log-file", help="Optional path to save organization log JSON.")
    organize_parser.add_argument("--pretty", action="store_true")
    organize_parser.set_defaults(func=run_organize)

    eval_parser = subparsers.add_parser("evaluate", help="Run Phase 9 evaluation.")
    eval_parser.add_argument("dataset_root")
    eval_parser.add_argument("ground_truth_csv")
    eval_parser.add_argument("--manual-baseline-csv")
    eval_parser.add_argument("--manual-seconds-per-file", type=float, default=20.0)
    eval_parser.add_argument("--manual-error-rate", type=float, default=0.12)
    eval_parser.add_argument("--pretty", action="store_true")
    eval_parser.set_defaults(func=run_evaluate)

    ui_parser = subparsers.add_parser("ui", help="Launch the desktop UI (Phase 7/8).")
    ui_parser.set_defaults(func=run_ui)

    undo_parser = subparsers.add_parser(
        "undo",
        help="Undo a previous organize run using its saved log file.",
    )
    undo_parser.add_argument("log_file", help="Path to organization log JSON.")
    undo_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview undo actions without changing files.",
    )
    undo_parser.add_argument(
        "--output-json",
        help="Optional path to save undo report JSON.",
    )
    undo_parser.add_argument("--pretty", action="store_true")
    undo_parser.set_defaults(func=run_undo)

    demo_parser = subparsers.add_parser(
        "demo",
        help="Run final end-to-end demonstration with input and organized output folders.",
    )
    demo_parser.add_argument(
        "--demo-root",
        default="final_demo",
        help="Directory where input/output demo folders are created.",
    )
    demo_parser.add_argument(
        "--generate-sample",
        action="store_true",
        help="Generate sample mixed files in input folder before running.",
    )
    demo_parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean demo root before running.",
    )
    demo_parser.add_argument("--output-json", help="Optional JSON output path.")
    demo_parser.add_argument("--pretty", action="store_true")
    demo_parser.set_defaults(func=run_demo)

    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
