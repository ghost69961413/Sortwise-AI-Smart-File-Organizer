#!/usr/bin/env python3
"""Phase 5: AI-style content analysis for smart file organization."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from file_type_detector import detect_file_type

try:
    from PIL import ExifTags, Image, ImageStat

    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

try:
    import cv2

    CV2_AVAILABLE = True
except ImportError:
    CV2_AVAILABLE = False

try:
    from pypdf import PdfReader

    PDF_LIB = "pypdf"
except ImportError:
    try:
        from PyPDF2 import PdfReader

        PDF_LIB = "PyPDF2"
    except ImportError:
        PdfReader = None
        PDF_LIB = None


TEXT_CLASS_KEYWORDS = {
    "finance": [
        "invoice",
        "receipt",
        "payment",
        "amount",
        "balance",
        "bank",
        "gst",
        "tax",
        "salary",
        "transaction",
        "bill",
        "budget",
    ],
    "legal": [
        "agreement",
        "contract",
        "clause",
        "party",
        "terms",
        "witness",
        "affidavit",
        "compliance",
        "nda",
        "policy",
    ],
    "academic": [
        "assignment",
        "exam",
        "lecture",
        "syllabus",
        "course",
        "research",
        "thesis",
        "university",
        "student",
        "grade",
    ],
    "work": [
        "project",
        "meeting",
        "minutes",
        "report",
        "proposal",
        "sprint",
        "roadmap",
        "client",
        "deliverable",
        "milestone",
    ],
    "personal": [
        "resume",
        "cv",
        "family",
        "medical",
        "passport",
        "id",
        "diary",
        "letter",
        "profile",
    ],
    "travel": [
        "flight",
        "hotel",
        "booking",
        "itinerary",
        "trip",
        "vacation",
        "travel",
        "boarding",
        "ticket",
    ],
}

IMAGE_TAG_KEYWORDS = {
    "screenshot": {"screenshot", "screen", "snip", "capture"},
    "document_scan": {"scan", "document", "receipt", "invoice", "paper", "form"},
    "food": {"food", "meal", "dinner", "lunch", "breakfast", "dish", "restaurant"},
    "nature": {"beach", "mountain", "forest", "sunset", "lake", "nature", "garden"},
    "travel": {"trip", "travel", "vacation", "journey", "hotel"},
    "people": {"selfie", "portrait", "family", "friends", "wedding", "party"},
    "pet": {"dog", "cat", "pet", "bird", "puppy", "kitten"},
    "work": {"whiteboard", "meeting", "office", "presentation"},
}

VIDEO_TAG_KEYWORDS = {
    "screen_recording": {"screenrecord", "screen_record", "screencast", "recording"},
    "meeting_recording": {"meeting", "zoom", "teams", "webex", "call", "standup"},
    "tutorial": {"tutorial", "howto", "lesson", "demo", "training", "course"},
    "travel": {"trip", "travel", "vacation", "beach", "mountain"},
    "event": {"wedding", "birthday", "party", "event", "celebration"},
    "social_media": {"reel", "short", "tiktok", "insta", "story", "vlog"},
}

WORD_PATTERN = re.compile(r"[a-z0-9]+")
TAG_PATTERN = re.compile(r"<[^>]+>")
ASCII_TEXT_PATTERN = re.compile(rb"[A-Za-z][A-Za-z0-9,\.\-\(\)\/ :]{4,}")


@dataclass(frozen=True)
class ContentAnalysisResult:
    path: str
    base_category: str
    content_category: str
    tags: list[str] = field(default_factory=list)
    confidence: float = 0.0
    summary: str = ""
    extracted_text_preview: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    signals: list[str] = field(default_factory=list)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        value = item.strip().lower()
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in WORD_PATTERN.findall(text.lower())}


def _name_tokens(path: Path) -> set[str]:
    return _tokenize(path.stem.replace("-", " ").replace("_", " "))


def _extract_pdf_text(path: Path, max_chars: int = 8000) -> tuple[str, list[str]]:
    signals: list[str] = []
    if PdfReader is not None:
        try:
            reader = PdfReader(str(path))
            text_chunks: list[str] = []
            for page in reader.pages[:8]:
                text_chunks.append(page.extract_text() or "")
            text = "\n".join(text_chunks).strip()
            if text:
                signals.append(f"Extracted text from PDF using {PDF_LIB}.")
                return text[:max_chars], signals
            signals.append(f"PDF text extraction via {PDF_LIB} returned empty text.")
        except Exception as error:  # pragma: no cover - defensive path
            signals.append(f"PDF parser failed ({PDF_LIB}): {error}")

    try:
        raw = path.read_bytes()[:500_000]
        ascii_chunks = ASCII_TEXT_PATTERN.findall(raw)
        if ascii_chunks:
            text = " ".join(chunk.decode("utf-8", errors="ignore") for chunk in ascii_chunks)
            signals.append("Extracted fallback ASCII text from raw PDF bytes.")
            return text[:max_chars], signals
        signals.append("No text found in raw PDF bytes (likely image-based scan).")
    except OSError as error:
        signals.append(f"Could not read PDF bytes: {error}")

    return "", signals


def _extract_docx_text(path: Path, max_chars: int = 8000) -> tuple[str, list[str]]:
    signals: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            xml_data = archive.read("word/document.xml")
        root = ElementTree.fromstring(xml_data)
        fragments = [node.text for node in root.iter() if node.tag.endswith("}t") and node.text]
        text = " ".join(fragments).strip()
        if text:
            signals.append("Extracted text from DOCX XML.")
            return text[:max_chars], signals
        signals.append("DOCX document contained no text fragments.")
    except Exception as error:  # pragma: no cover - defensive path
        signals.append(f"DOCX extraction failed: {error}")
    return "", signals


def _extract_text_file(path: Path, max_chars: int = 8000) -> tuple[str, list[str]]:
    signals: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if path.suffix.lower() in {".htm", ".html", ".xml"}:
            text = TAG_PATTERN.sub(" ", text)
            signals.append("Removed markup tags from text.")
        signals.append("Read text document directly.")
        return text[:max_chars], signals
    except OSError as error:
        signals.append(f"Could not read text file: {error}")
        return "", signals


def _extract_document_text(path: Path, max_chars: int = 8000) -> tuple[str, list[str]]:
    ext = path.suffix.lower()
    if ext == ".pdf":
        return _extract_pdf_text(path, max_chars=max_chars)
    if ext == ".docx":
        return _extract_docx_text(path, max_chars=max_chars)
    if ext in {
        ".txt",
        ".md",
        ".csv",
        ".tsv",
        ".json",
        ".yaml",
        ".yml",
        ".xml",
        ".htm",
        ".html",
        ".rtf",
    }:
        return _extract_text_file(path, max_chars=max_chars)
    return "", ["No text extractor configured for this document subtype."]


def _classify_text_content(path: Path, text: str) -> tuple[str, list[str], float, list[str]]:
    filename_tokens = _name_tokens(path)
    text_tokens = _tokenize(text)
    lowered = text.lower()

    scores: dict[str, int] = {}
    match_tags: list[str] = []
    signals: list[str] = []

    for category, keywords in TEXT_CLASS_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            if keyword in text_tokens:
                score += 3
                match_tags.append(keyword)
            elif keyword in filename_tokens:
                score += 2
                match_tags.append(keyword)
            elif keyword in lowered:
                score += 1
        scores[category] = score

    best_category = max(scores, key=scores.get) if scores else "general"
    best_score = scores.get(best_category, 0)
    second_score = sorted(scores.values(), reverse=True)[1] if len(scores) > 1 else 0

    if best_score <= 1:
        signals.append("No strong keyword signal; fallback to general_document.")
        return "general_document", _unique(match_tags), 0.55, signals

    margin = best_score - second_score
    confidence = 0.65 + min(0.25, best_score * 0.02) + min(0.08, margin * 0.02)
    confidence = min(0.95, round(confidence, 2))
    signals.append(
        f"Top text category '{best_category}' with score {best_score} and margin {margin}."
    )
    return best_category, _unique(match_tags), confidence, signals


def _analyze_document(path: Path, base_category: str) -> ContentAnalysisResult:
    text, extraction_signals = _extract_document_text(path)
    content_category, tags, confidence, scoring_signals = _classify_text_content(path, text)

    preview = re.sub(r"\s+", " ", text).strip()[:240]
    summary = (
        f"{base_category.upper()} analyzed via text extraction and keyword scoring."
        if text
        else f"{base_category.upper()} analyzed with limited text signals."
    )

    metadata = {
        "size_bytes": path.stat().st_size,
        "text_chars_extracted": len(text),
        "extractor": "text+keywords",
    }

    signals = extraction_signals + scoring_signals
    if not text:
        tags = _unique(tags + ["needs_ocr_review"])

    return ContentAnalysisResult(
        path=str(path),
        base_category=base_category,
        content_category=content_category,
        tags=tags,
        confidence=confidence if text else min(confidence, 0.58),
        summary=summary,
        extracted_text_preview=preview,
        metadata=metadata,
        signals=signals,
    )


def _analyze_image(path: Path) -> ContentAnalysisResult:
    tags: list[str] = []
    signals: list[str] = []
    metadata: dict[str, Any] = {"size_bytes": path.stat().st_size}
    content_category = "general_photo"
    confidence = 0.6

    name_tokens = _name_tokens(path)
    for tag, keywords in IMAGE_TAG_KEYWORDS.items():
        if name_tokens.intersection(keywords):
            tags.append(tag)

    if "screenshot" in tags:
        content_category = "screenshot"
        confidence = 0.88
        signals.append("Filename tokens suggest screenshot.")
    elif "document_scan" in tags:
        content_category = "document_scan"
        confidence = 0.84
        signals.append("Filename tokens suggest scanned document image.")
    elif "nature" in tags or "travel" in tags:
        content_category = "travel_or_nature_photo"
        confidence = 0.76
        signals.append("Filename keywords suggest travel/nature context.")
    elif "people" in tags:
        content_category = "people_photo"
        confidence = 0.74
        signals.append("Filename keywords suggest people-focused image.")

    if PIL_AVAILABLE:
        try:
            with Image.open(path) as image:
                width, height = image.size
                metadata["width"] = width
                metadata["height"] = height
                metadata["mode"] = image.mode

                ratio = width / height if height else 1.0
                if ratio > 1.25:
                    tags.append("landscape")
                elif ratio < 0.8:
                    tags.append("portrait")
                else:
                    tags.append("squareish")

                luminance = ImageStat.Stat(image.convert("L")).mean[0]
                metadata["avg_luminance"] = round(float(luminance), 2)
                if luminance < 70:
                    tags.append("low_light")
                elif luminance > 180:
                    tags.append("bright_scene")

                exif_data = image.getexif()
                if exif_data:
                    exif = {
                        ExifTags.TAGS.get(tag, str(tag)): value
                        for tag, value in exif_data.items()
                        if tag in ExifTags.TAGS
                    }
                    make = exif.get("Make")
                    model = exif.get("Model")
                    if make or model:
                        metadata["camera"] = " ".join(
                            part for part in [str(make or ""), str(model or "")] if part
                        ).strip()
                        tags.append("camera_photo")
                    if exif.get("GPSInfo"):
                        tags.append("geo_tagged")
                        if content_category == "general_photo":
                            content_category = "travel_or_nature_photo"
                        confidence = max(confidence, 0.79)
                signals.append("Image metadata and visual statistics analyzed via Pillow.")
        except Exception as error:  # pragma: no cover - defensive path
            signals.append(f"Could not analyze image metadata: {error}")
    else:
        signals.append("Pillow unavailable; used filename-based image logic only.")

    tags = _unique(tags) or ["photo"]
    summary = "Image analyzed using filename semantics and available metadata."
    return ContentAnalysisResult(
        path=str(path),
        base_category="image",
        content_category=content_category,
        tags=tags,
        confidence=round(min(0.95, confidence), 2),
        summary=summary,
        extracted_text_preview="",
        metadata=metadata,
        signals=signals,
    )


def _ffprobe_metadata(path: Path) -> tuple[dict[str, Any], list[str]]:
    metadata: dict[str, Any] = {}
    signals: list[str] = []
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=width,height,codec_name",
        "-of",
        "json",
        str(path),
    ]
    try:
        output = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except FileNotFoundError:
        signals.append("ffprobe not available.")
        return metadata, signals
    except subprocess.TimeoutExpired:
        signals.append("ffprobe timed out.")
        return metadata, signals

    if output.returncode != 0:
        stderr = output.stderr.strip()[:180]
        signals.append(f"ffprobe returned non-zero exit code: {stderr or output.returncode}")
        return metadata, signals

    try:
        parsed = json.loads(output.stdout)
        streams = parsed.get("streams", [])
        if streams:
            stream = streams[0]
            width = stream.get("width")
            height = stream.get("height")
            codec = stream.get("codec_name")
            if width:
                metadata["width"] = width
            if height:
                metadata["height"] = height
            if codec:
                metadata["codec"] = codec
        fmt = parsed.get("format", {})
        duration = fmt.get("duration")
        if duration is not None:
            metadata["duration_seconds"] = round(float(duration), 2)
        signals.append("Extracted video metadata with ffprobe.")
    except (ValueError, TypeError) as error:
        signals.append(f"Could not parse ffprobe JSON: {error}")
    return metadata, signals


def _video_preview_hints(path: Path) -> tuple[dict[str, Any], list[str], list[str]]:
    metadata: dict[str, Any] = {}
    tags: list[str] = []
    signals: list[str] = []
    if not CV2_AVAILABLE:
        signals.append("OpenCV unavailable; skipped preview frame analysis.")
        return metadata, tags, signals

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        signals.append("Could not open video with OpenCV.")
        return metadata, tags, signals

    ok, frame = capture.read()
    capture.release()
    if not ok or frame is None:
        signals.append("Could not decode preview frame.")
        return metadata, tags, signals

    avg_brightness = float(frame.mean())
    metadata["preview_avg_brightness"] = round(avg_brightness, 2)
    if avg_brightness < 70:
        tags.append("dark_scene")
    elif avg_brightness > 180:
        tags.append("bright_scene")

    signals.append("Analyzed first preview frame with OpenCV.")
    return metadata, tags, signals


def _analyze_video(path: Path) -> ContentAnalysisResult:
    tags: list[str] = []
    signals: list[str] = []
    metadata: dict[str, Any] = {"size_bytes": path.stat().st_size}
    content_category = "general_video"
    confidence = 0.6

    name_tokens = _name_tokens(path)
    match_scores: dict[str, int] = {}
    for tag, keywords in VIDEO_TAG_KEYWORDS.items():
        matched = name_tokens.intersection(keywords)
        if matched:
            tags.append(tag)
            match_scores[tag] = len(matched)

    if match_scores:
        priority = {
            "meeting_recording": 6,
            "screen_recording": 5,
            "tutorial": 4,
            "social_media": 3,
            "event": 2,
            "travel": 1,
        }
        winner = max(match_scores, key=lambda tag: (match_scores[tag], priority.get(tag, 0)))
        category_map = {
            "screen_recording": "screen_recording",
            "meeting_recording": "meeting_recording",
            "tutorial": "tutorial_video",
            "event": "event_video",
            "travel": "travel_video",
            "social_media": "social_clip",
        }
        confidence_map = {
            "screen_recording": 0.86,
            "meeting_recording": 0.84,
            "tutorial": 0.8,
            "event": 0.77,
            "travel": 0.76,
            "social_media": 0.78,
        }
        content_category = category_map[winner]
        confidence = confidence_map[winner]
        signals.append(
            f"Video category selected by filename keyword score: {winner} ({match_scores[winner]} hits)."
        )

    ffprobe_data, ffprobe_signals = _ffprobe_metadata(path)
    metadata.update(ffprobe_data)
    signals.extend(ffprobe_signals)

    duration = metadata.get("duration_seconds")
    if isinstance(duration, (float, int)):
        if duration < 30:
            tags.append("very_short")
        elif duration < 180:
            tags.append("short")
        elif duration > 1200:
            tags.append("long_form")
        confidence = max(confidence, 0.69)

    preview_meta, preview_tags, preview_signals = _video_preview_hints(path)
    metadata.update(preview_meta)
    tags.extend(preview_tags)
    signals.extend(preview_signals)

    tags = _unique(tags) or ["video"]
    summary = "Video analyzed using filename semantics, metadata, and optional preview hints."
    return ContentAnalysisResult(
        path=str(path),
        base_category="video",
        content_category=content_category,
        tags=tags,
        confidence=round(min(0.95, confidence), 2),
        summary=summary,
        extracted_text_preview="",
        metadata=metadata,
        signals=signals,
    )


def analyze_file_content(file_path: str | Path) -> ContentAnalysisResult:
    """Analyze file content to produce semantic category and tags."""
    path = Path(file_path).expanduser().resolve()
    detection = detect_file_type(path)
    base_category = detection.category

    if base_category == "image":
        return _analyze_image(path)
    if base_category == "video":
        return _analyze_video(path)
    if base_category in {"pdf", "document"}:
        return _analyze_document(path, base_category=base_category)

    return ContentAnalysisResult(
        path=str(path),
        base_category=base_category,
        content_category="unclassified",
        tags=["unsupported_type"],
        confidence=0.0,
        summary="Unsupported or unknown base type for content analysis.",
        metadata={"size_bytes": path.stat().st_size},
        signals=["File type detector returned unknown/unsupported category."],
    )


def analyze_directory_content(
    directory: str | Path, recursive: bool = True
) -> list[ContentAnalysisResult]:
    """Analyze all files in a directory and return content-analysis results."""
    root = Path(directory).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Directory not found: {root}")
    if not root.is_dir():
        raise ValueError(f"Path is not a directory: {root}")

    pattern = "**/*" if recursive else "*"
    results: list[ContentAnalysisResult] = []
    for file_path in root.glob(pattern):
        if file_path.is_file():
            results.append(analyze_file_content(file_path))
    return results


def _summarize(results: list[ContentAnalysisResult]) -> dict[str, Any]:
    base_counts: dict[str, int] = {}
    content_counts: dict[str, int] = {}
    for result in results:
        base_counts[result.base_category] = base_counts.get(result.base_category, 0) + 1
        content_counts[result.content_category] = content_counts.get(result.content_category, 0) + 1
    return {
        "total_files": len(results),
        "base_category_counts": base_counts,
        "content_category_counts": content_counts,
    }


def _build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 5 smart content analyzer for mixed files."
    )
    parser.add_argument("path", help="Path to a file or directory")
    parser.add_argument(
        "--non-recursive",
        action="store_true",
        help="Scan only top-level files for directory targets.",
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
    indent = 2 if args.pretty else None

    if target.is_file():
        result = analyze_file_content(target)
        print(json.dumps(asdict(result), indent=indent))
        return 0

    if target.is_dir():
        results = analyze_directory_content(target, recursive=not args.non_recursive)
        payload = {
            "summary": _summarize(results),
            "files": [asdict(result) for result in results],
        }
        print(json.dumps(payload, indent=indent))
        return 0

    parser.error(f"Path does not exist: {target}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
