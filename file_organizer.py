#!/usr/bin/env python3
"""Phase 6: Automatic classification and folder organization."""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from content_analyzer import ContentAnalysisResult, analyze_file_content


@dataclass(frozen=True)
class OrganizationDecision:
    source_path: str
    destination_path: str
    folder_path: str
    action: str
    base_category: str
    content_category: str
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    reason: str = ""
    needs_manual_review: bool = False
    review_reason: str = ""


IMAGE_EVENT_KEYWORDS = {
    "wedding",
    "birthday",
    "party",
    "event",
    "celebration",
    "festival",
    "ceremony",
    "anniversary",
}

BILL_KEYWORDS = {
    "bill",
    "invoice",
    "receipt",
    "payment",
    "tax",
    "gst",
    "salary",
    "transaction",
    "amount",
}

REPORT_KEYWORDS = {
    "report",
    "proposal",
    "analysis",
    "minutes",
    "meeting",
    "project",
    "roadmap",
    "deliverable",
    "contract",
    "agreement",
}

RESUME_KEYWORDS = {"resume", "cv", "curriculumvitae", "portfolio", "profile"}
ASSIGNMENT_KEYWORDS = {
    "assignment",
    "homework",
    "project",
    "thesis",
    "syllabus",
    "lecture",
    "exam",
    "course",
    "research",
    "student",
}
LETTER_KEYWORDS = {
    "letter",
    "application",
    "recommendation",
    "cover",
    "request",
    "notice",
    "invitation",
    "memo",
    "mail",
}

REVIEW_CONFIDENCE_THRESHOLD = 0.62
LOW_SIGNAL_CONTENT_CATEGORIES = {
    "general_document",
    "general_photo",
    "general_video",
    "unclassified",
}


def _tokens_from_stem(path: Path) -> set[str]:
    cleaned = path.stem.lower().replace("-", " ").replace("_", " ")
    return {token for token in cleaned.split() if token}


def _contains_any(values: set[str], needles: set[str]) -> bool:
    return bool(values.intersection(needles))


def _classify_image(analysis: ContentAnalysisResult, source: Path) -> tuple[str, str]:
    token_set = _tokens_from_stem(source)
    tag_set = set(analysis.tags)

    if (
        analysis.content_category == "travel_or_nature_photo"
        or _contains_any(tag_set, {"nature", "travel", "geo_tagged"})
    ):
        return "Images/Nature", "Mapped to nature using travel/nature content signals."

    if _contains_any(token_set, IMAGE_EVENT_KEYWORDS) or _contains_any(
        tag_set, {"event", "celebration"}
    ):
        return "Images/Events", "Mapped to events using event keywords in filename/tags."

    return "Images/Personal", "Mapped to personal image bucket by default."


def _classify_pdf(analysis: ContentAnalysisResult) -> tuple[str, str]:
    tag_set = set(analysis.tags)
    content = analysis.content_category

    if content == "finance" or _contains_any(tag_set, BILL_KEYWORDS):
        return "PDFs/Bills", "Mapped to bills using finance keywords."

    if content in {"work", "legal"} or _contains_any(tag_set, REPORT_KEYWORDS):
        return "PDFs/Reports", "Mapped to reports using work/legal/report signals."

    return "PDFs/Notes", "Mapped to notes by default."


def _classify_document(analysis: ContentAnalysisResult, source: Path) -> tuple[str, str]:
    token_set = _tokens_from_stem(source)
    tag_set = set(analysis.tags)
    content = analysis.content_category

    if _contains_any(tag_set, RESUME_KEYWORDS) or _contains_any(token_set, RESUME_KEYWORDS):
        return "Documents/Resume", "Mapped to resume using resume/CV keywords."

    if content == "academic" or _contains_any(tag_set, ASSIGNMENT_KEYWORDS) or _contains_any(
        token_set, ASSIGNMENT_KEYWORDS
    ):
        return "Documents/Assignments", "Mapped to assignments using academic keywords."

    if _contains_any(tag_set, LETTER_KEYWORDS) or _contains_any(token_set, LETTER_KEYWORDS):
        return "Documents/Letters", "Mapped to letters using correspondence keywords."

    return "Documents/General", "Mapped to general documents by default."


def _classify_video(analysis: ContentAnalysisResult) -> tuple[str, str]:
    content = analysis.content_category
    if content == "meeting_recording":
        return "Videos/Meetings", "Mapped to meetings from content category."
    if content == "screen_recording":
        return "Videos/ScreenRecordings", "Mapped to screen recordings from content category."
    if content == "tutorial_video":
        return "Videos/Tutorials", "Mapped to tutorials from content category."
    if content == "event_video":
        return "Videos/Events", "Mapped to events from content category."
    if content == "travel_video":
        return "Videos/Travel", "Mapped to travel videos from content category."
    if content == "social_clip":
        return "Videos/SocialClips", "Mapped to social clips from content category."
    return "Videos/General", "Mapped to general videos by default."


def _review_reason(analysis: ContentAnalysisResult) -> str:
    reasons: list[str] = []
    if analysis.base_category in {"unknown"}:
        reasons.append("unknown file type")
    if analysis.content_category in LOW_SIGNAL_CONTENT_CATEGORIES:
        reasons.append("low-signal content category")
    if analysis.confidence < REVIEW_CONFIDENCE_THRESHOLD:
        reasons.append(f"low confidence ({analysis.confidence:.2f})")
    if "needs_ocr_review" in set(analysis.tags):
        reasons.append("OCR review needed")
    return "; ".join(reasons)


def choose_folder(analysis: ContentAnalysisResult, source_path: Path) -> tuple[str, str, bool, str]:
    """Return folder path, reason, and manual-review signals."""
    review_reason = _review_reason(analysis)
    if review_reason:
        return (
            "Others/NeedsReview",
            "Routed to Others for manual verification due to uncertain classification.",
            True,
            review_reason,
        )

    if analysis.base_category == "image":
        folder, reason = _classify_image(analysis, source_path)
        return folder, reason, False, ""
    if analysis.base_category == "pdf":
        folder, reason = _classify_pdf(analysis)
        return folder, reason, False, ""
    if analysis.base_category == "document":
        folder, reason = _classify_document(analysis, source_path)
        return folder, reason, False, ""
    if analysis.base_category == "video":
        folder, reason = _classify_video(analysis)
        return folder, reason, False, ""
    return (
        "Others/Uncategorized",
        "Base category unsupported; routed to fallback.",
        True,
        "unsupported base category",
    )


def _sanitize_relative_folder(folder_path: str) -> str:
    raw = folder_path.strip().replace("\\", "/")
    if not raw:
        raise ValueError("Manual folder override cannot be empty.")

    parts: list[str] = []
    for part in raw.split("/"):
        value = part.strip()
        if not value or value == ".":
            continue
        if value == "..":
            raise ValueError("Manual folder override cannot contain '..'.")
        parts.append(value)

    if not parts:
        raise ValueError("Manual folder override cannot be empty.")
    return "/".join(parts)


def _safe_destination_path(destination_dir: Path, source_file: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    candidate = destination_dir / source_file.name
    if not candidate.exists():
        return candidate

    stem = source_file.stem
    suffix = source_file.suffix
    index = 1
    while True:
        candidate = destination_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def organize_file(
    file_path: str | Path,
    destination_root: str | Path,
    move_files: bool = False,
    dry_run: bool = False,
) -> OrganizationDecision:
    """Analyze one file and place it into the correct category folder."""
    source = Path(file_path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(f"File not found: {source}")
    if not source.is_file():
        raise ValueError(f"Path is not a file: {source}")

    destination_base = Path(destination_root).expanduser().resolve()
    analysis = analyze_file_content(source)
    folder_path, reason, needs_manual_review, review_reason = choose_folder(analysis, source)
    destination_dir = destination_base / folder_path
    preferred_destination = destination_dir / source.name

    if preferred_destination.resolve() == source:
        destination = source
        action = "skipped"
        reason = "Source already in destination folder; no action taken."
    else:
        destination = _safe_destination_path(destination_dir, source)
        if dry_run:
            action = "would_move" if move_files else "would_copy"
        else:
            if move_files:
                shutil.move(str(source), str(destination))
                action = "moved"
            else:
                shutil.copy2(str(source), str(destination))
                action = "copied"

    return OrganizationDecision(
        source_path=str(source),
        destination_path=str(destination),
        folder_path=folder_path,
        action=action,
        base_category=analysis.base_category,
        content_category=analysis.content_category,
        tags=analysis.tags,
        confidence=analysis.confidence,
        reason=reason,
        needs_manual_review=needs_manual_review,
        review_reason=review_reason,
    )


def preview_manual_override(
    decision: OrganizationDecision,
    destination_root: str | Path,
    manual_folder: str,
) -> OrganizationDecision:
    """Return an updated decision preview using a user-selected folder."""
    destination_base = Path(destination_root).expanduser().resolve()
    cleaned_folder = _sanitize_relative_folder(manual_folder)
    destination_dir = destination_base / cleaned_folder

    current_path = Path(decision.destination_path).expanduser().resolve()
    if not current_path.exists():
        current_path = Path(decision.source_path).expanduser().resolve()

    new_destination = destination_dir / current_path.name
    if new_destination == current_path:
        action = "skipped_override"
    elif decision.action.startswith("would_"):
        action = "would_override"
    else:
        action = "preview_override"

    return OrganizationDecision(
        source_path=decision.source_path,
        destination_path=str(new_destination),
        folder_path=cleaned_folder,
        action=action,
        base_category=decision.base_category,
        content_category=decision.content_category,
        tags=decision.tags,
        confidence=decision.confidence,
        reason="Manual override preview selected by user.",
        needs_manual_review=False,
        review_reason="",
    )


def apply_manual_override(
    decision: OrganizationDecision,
    destination_root: str | Path,
    manual_folder: str,
    dry_run: bool = False,
) -> OrganizationDecision:
    """Apply manual correction by moving the file to a user-selected folder."""
    destination_base = Path(destination_root).expanduser().resolve()
    cleaned_folder = _sanitize_relative_folder(manual_folder)
    destination_dir = destination_base / cleaned_folder

    candidate = Path(decision.destination_path).expanduser().resolve()
    if not candidate.exists() or not candidate.is_file():
        candidate = Path(decision.source_path).expanduser().resolve()
    if not candidate.exists() or not candidate.is_file():
        raise FileNotFoundError(
            "Could not find a file to override. Source and destination files are missing."
        )

    preferred_destination = destination_dir / candidate.name
    if preferred_destination.resolve() == candidate:
        destination = candidate
        action = "skipped_override"
    else:
        destination = _safe_destination_path(destination_dir, candidate)
        if dry_run:
            action = "would_move_override"
        else:
            shutil.move(str(candidate), str(destination))
            action = "moved_override"

    return OrganizationDecision(
        source_path=decision.source_path,
        destination_path=str(destination),
        folder_path=cleaned_folder,
        action=action,
        base_category=decision.base_category,
        content_category=decision.content_category,
        tags=decision.tags,
        confidence=decision.confidence,
        reason="Manual override applied by user.",
        needs_manual_review=False,
        review_reason="",
    )


def organize_directory(
    source_directory: str | Path,
    destination_root: str | Path,
    recursive: bool = True,
    move_files: bool = False,
    dry_run: bool = False,
    progress_callback: Callable[[int, int], None] | None = None,
) -> list[OrganizationDecision]:
    """Analyze and organize files, optionally reporting completed and total counts."""
    source_root = Path(source_directory).expanduser().resolve()
    destination_base = Path(destination_root).expanduser().resolve()

    if not source_root.exists():
        raise FileNotFoundError(f"Source directory not found: {source_root}")
    if not source_root.is_dir():
        raise ValueError(f"Source path is not a directory: {source_root}")

    pattern = "**/*" if recursive else "*"
    files = [
        path for path in source_root.glob(pattern)
        if path.is_file() and not _is_relative_to(path.resolve(), destination_base)
    ]

    decisions: list[OrganizationDecision] = []
    total = len(files)
    if progress_callback:
        progress_callback(0, total)
    for completed, path in enumerate(files, start=1):
        decisions.append(
            organize_file(
                path,
                destination_root=destination_base,
                move_files=move_files,
                dry_run=dry_run,
            )
        )
        if progress_callback:
            progress_callback(completed, total)
    return decisions


def _summarize(decisions: list[OrganizationDecision]) -> dict[str, Any]:
    action_counts: dict[str, int] = {}
    folder_counts: dict[str, int] = {}
    review_required_count = 0
    for decision in decisions:
        action_counts[decision.action] = action_counts.get(decision.action, 0) + 1
        folder_counts[decision.folder_path] = folder_counts.get(decision.folder_path, 0) + 1
        if decision.needs_manual_review:
            review_required_count += 1
    return {
        "total_files": len(decisions),
        "action_counts": action_counts,
        "folder_counts": folder_counts,
        "needs_manual_review": review_required_count,
    }


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 6 file organizer: classify and place files into smart folders."
    )
    parser.add_argument("source", help="Source file or directory to organize")
    parser.add_argument("destination", help="Destination root directory for organized files")
    parser.add_argument(
        "--move",
        action="store_true",
        help="Move files instead of copying.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview organization actions without file writes.",
    )
    parser.add_argument(
        "--non-recursive",
        action="store_true",
        help="Do not recurse into subdirectories when source is a directory.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON output.",
    )
    return parser


def main() -> int:
    parser = _build_cli()
    args = parser.parse_args()

    source = Path(args.source).expanduser()
    destination = Path(args.destination).expanduser()
    indent = 2 if args.pretty else None

    if source.is_file():
        decision = organize_file(
            file_path=source,
            destination_root=destination,
            move_files=args.move,
            dry_run=args.dry_run,
        )
        print(json.dumps(asdict(decision), indent=indent))
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
            "summary": _summarize(decisions),
            "files": [asdict(decision) for decision in decisions],
        }
        print(json.dumps(payload, indent=indent))
        return 0

    parser.error(f"Source path does not exist: {source}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
