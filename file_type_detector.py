#!/usr/bin/env python3
"""Phase 4: File type detection module for AI file organizer."""

from __future__ import annotations

import argparse
import json
import mimetypes
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


IMAGE_EXTENSIONS = {
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".jpeg",
    ".jpg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}

VIDEO_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".flv",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".webm",
    ".wmv",
}

DOCUMENT_EXTENSIONS = {
    ".csv",
    ".doc",
    ".docx",
    ".htm",
    ".html",
    ".json",
    ".md",
    ".odt",
    ".ppt",
    ".pptx",
    ".rtf",
    ".tsv",
    ".txt",
    ".xls",
    ".xlsx",
    ".xml",
    ".yaml",
    ".yml",
}

DOCUMENT_MIME_TYPES = {
    "application/msword",
    "application/rtf",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.oasis.opendocument.text",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/xml",
    "application/json",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/plain",
    "text/tab-separated-values",
    "text/xml",
}


@dataclass(frozen=True)
class DetectionResult:
    path: str
    category: str
    extension: str
    mime_type: str | None
    confidence: float
    method: str
    reason: str


def _category_from_extension(ext: str) -> str | None:
    if ext == ".pdf":
        return "pdf"
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    if ext in DOCUMENT_EXTENSIONS:
        return "document"
    return None


def _category_from_mime(mime_type: str | None) -> str | None:
    if not mime_type:
        return None
    if mime_type == "application/pdf":
        return "pdf"
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type in DOCUMENT_MIME_TYPES or mime_type.startswith("text/"):
        return "document"
    return None


def _category_from_signature(path: Path) -> tuple[str | None, str]:
    """Inspect the file header to infer category when possible."""
    try:
        header = path.read_bytes()[:64]
    except OSError:
        return None, "Could not read file bytes."

    if header.startswith(b"%PDF-"):
        return "pdf", "Matched PDF file signature."
    if header.startswith(b"\xff\xd8\xff"):
        return "image", "Matched JPEG signature."
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "Matched PNG signature."
    if header.startswith((b"GIF87a", b"GIF89a")):
        return "image", "Matched GIF signature."
    if header.startswith(b"BM"):
        return "image", "Matched BMP signature."
    if len(header) >= 12 and header[0:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image", "Matched WEBP signature."
    if header.startswith((b"II*\x00", b"MM\x00*")):
        return "image", "Matched TIFF signature."

    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video", "Matched MP4/MOV family signature."
    if len(header) >= 12 and header[0:4] == b"RIFF" and header[8:11] == b"AVI":
        return "video", "Matched AVI signature."
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "video", "Matched Matroska/WebM signature."

    return None, "No known signature matched."


def detect_file_type(file_path: str | Path) -> DetectionResult:
    """Classify a file into image, video, pdf, document, or unknown."""
    path = Path(file_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not path.is_file():
        raise ValueError(f"Path is not a file: {path}")

    ext = path.suffix.lower()
    mime_type, _ = mimetypes.guess_type(str(path))

    ext_category = _category_from_extension(ext)
    mime_category = _category_from_mime(mime_type)
    signature_category, signature_reason = _category_from_signature(path)

    if signature_category:
        if (
            ext_category
            and ext_category != signature_category
            or mime_category
            and mime_category != signature_category
        ):
            return DetectionResult(
                path=str(path),
                category=signature_category,
                extension=ext,
                mime_type=mime_type,
                confidence=0.9,
                method="signature",
                reason=(
                    f"{signature_reason} Extension/MIME disagreed; signature prioritized."
                ),
            )
        return DetectionResult(
            path=str(path),
            category=signature_category,
            extension=ext,
            mime_type=mime_type,
            confidence=0.98,
            method="signature",
            reason=signature_reason,
        )

    if ext_category and mime_category and ext_category == mime_category:
        return DetectionResult(
            path=str(path),
            category=ext_category,
            extension=ext,
            mime_type=mime_type,
            confidence=0.88,
            method="extension+mime",
            reason="Extension and MIME type matched.",
        )

    if mime_category:
        return DetectionResult(
            path=str(path),
            category=mime_category,
            extension=ext,
            mime_type=mime_type,
            confidence=0.84,
            method="mime",
            reason="Detected from MIME type.",
        )

    if ext_category:
        return DetectionResult(
            path=str(path),
            category=ext_category,
            extension=ext,
            mime_type=mime_type,
            confidence=0.72,
            method="extension",
            reason="Detected from extension.",
        )

    return DetectionResult(
        path=str(path),
        category="unknown",
        extension=ext,
        mime_type=mime_type,
        confidence=0.0,
        method="none",
        reason="Could not determine type from signature, MIME, or extension.",
    )


def scan_directory(directory: str | Path, recursive: bool = True) -> list[DetectionResult]:
    """Scan files in a directory and return detection results."""
    root = Path(directory).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Directory not found: {root}")
    if not root.is_dir():
        raise ValueError(f"Path is not a directory: {root}")

    pattern = "**/*" if recursive else "*"
    results: list[DetectionResult] = []

    for item in root.glob(pattern):
        if item.is_file():
            results.append(detect_file_type(item))
    return results


def _summarize(results: Iterable[DetectionResult]) -> dict[str, int]:
    summary = {"image": 0, "video": 0, "pdf": 0, "document": 0, "unknown": 0}
    for result in results:
        summary[result.category] = summary.get(result.category, 0) + 1
    return summary


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect file category for AI organizer Phase 4."
    )
    parser.add_argument("path", help="Path to a file or directory")
    parser.add_argument(
        "--non-recursive",
        action="store_true",
        help="Scan only top-level files when a directory is passed.",
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
    target = Path(args.path).expanduser()
    pretty = 2 if args.pretty else None

    if target.is_file():
        result = detect_file_type(target)
        print(json.dumps(asdict(result), indent=pretty))
        return 0

    if target.is_dir():
        results = scan_directory(target, recursive=not args.non_recursive)
        payload = {
            "summary": _summarize(results),
            "files": [asdict(result) for result in results],
        }
        print(json.dumps(payload, indent=pretty))
        return 0

    parser.error(f"Path does not exist: {target}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
