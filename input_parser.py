from __future__ import annotations

import io
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable

from docx import Document
from docx.document import Document as _Document
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P

ALLOWED_EXTENSIONS = {".docx", ".vtt", ".txt"}
ROLE_NAMES = ("pagetext", "transcript", "quiz")


@dataclass
class FileRecord:
    name: str
    path: str
    relative_path: str
    extension: str
    lesson_id: str = ""
    chapter_key: str = ""
    role_scores: dict[str, float] = field(default_factory=dict)


@dataclass
class LessonBundle:
    lesson_id: str
    chapter_key: str
    pagetext: FileRecord | None = None
    transcript: FileRecord | None = None
    quiz: FileRecord | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return bool(self.pagetext and self.transcript and self.quiz and not self.errors)


# -----------------------------------------------------------------------------
# Generic text readers
# -----------------------------------------------------------------------------


def iter_block_items(parent):
    """Yield Paragraph/Table blocks in source order for a DOCX document/cell."""
    if isinstance(parent, _Document):
        parent_elm = parent.element.body
    elif isinstance(parent, _Cell):
        parent_elm = parent._tc
    else:
        raise TypeError("parent must be a python-docx Document or Cell")

    for child in parent_elm.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def read_docx_lines(path: str) -> tuple[list[str], str]:
    """Read paragraphs and table-cell text in document order.

    Returns (lines, error). Embedded line breaks are split so quiz option lines
    remain independently parseable.
    """
    try:
        doc = Document(path)
        lines: list[str] = []
        for block in iter_block_items(doc):
            if isinstance(block, Paragraph):
                raw = block.text
                for piece in raw.splitlines() or [raw]:
                    if piece.strip():
                        lines.append(piece.strip())
            else:
                for row in block.rows:
                    for cell in row.cells:
                        for raw in cell.text.splitlines():
                            if raw.strip():
                                lines.append(raw.strip())
        return lines, ""
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"


def read_docx_text(path: str) -> tuple[str, str]:
    lines, error = read_docx_lines(path)
    return "\n".join(lines), error


def read_vtt_text(path: str) -> tuple[str, str]:
    """Convert WebVTT/closed captions to clean transcript text."""
    try:
        raw = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    except Exception as exc:
        return "", f"{type(exc).__name__}: {exc}"

    lines = raw.splitlines()
    kept: list[str] = []
    in_note = False
    timestamp_re = re.compile(
        r"^\s*(?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3}\s*-->\s*(?:\d{1,2}:)?\d{2}:\d{2}[\.,]\d{3}"
    )
    cue_number_re = re.compile(r"^\s*\d+\s*$")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            in_note = False
            continue
        if stripped.upper().startswith("WEBVTT"):
            continue
        if stripped.startswith("NOTE"):
            in_note = True
            continue
        if in_note:
            continue
        if timestamp_re.match(stripped):
            continue
        if cue_number_re.match(stripped):
            continue
        # Remove common VTT inline tags while preserving spoken text.
        stripped = re.sub(r"<[^>]+>", "", stripped)
        stripped = re.sub(r"\s+", " ", stripped).strip()
        if stripped:
            kept.append(stripped)

    # Caption line breaks frequently split sentences. Joining with spaces gives the
    # generator the same spoken content without timestamp noise.
    text = re.sub(r"\s+", " ", " ".join(kept)).strip()
    return text, ""


def read_transcript_source(path: str) -> tuple[str, str]:
    ext = Path(path).suffix.lower()
    if ext == ".docx":
        return read_docx_text(path)
    if ext in {".vtt", ".txt"}:
        if ext == ".vtt":
            return read_vtt_text(path)
        try:
            return Path(path).read_text(encoding="utf-8-sig", errors="replace").strip(), ""
        except Exception as exc:
            return "", f"{type(exc).__name__}: {exc}"
    return "", f"Unsupported transcript extension: {ext}"


# -----------------------------------------------------------------------------
# Upload extraction and lesson grouping
# -----------------------------------------------------------------------------


def _clean_parts(member: str) -> list[str]:
    parts: list[str] = []
    for part in Path(member).parts:
        if part in {"", "."}:
            continue
        if part == "__MACOSX":
            return []
        parts.append(part)
    return parts


def _lesson_id_from_parts(relative_path: str) -> str:
    parts = list(Path(relative_path).parts)
    # Prefer a numeric lesson directory nearest the file.
    for part in reversed(parts[:-1]):
        clean = part.strip()
        if re.fullmatch(r"\d{3,9}", clean):
            return clean
    # Fall back to the first standalone numeric token in the filename.
    stem = Path(parts[-1] if parts else relative_path).stem
    match = re.search(r"(?<!\d)(\d{3,9})(?!\d)", stem)
    return match.group(1) if match else ""


def _chapter_key_from_parts(relative_path: str, lesson_id: str) -> str:
    parts = list(Path(relative_path).parts)
    if not parts:
        return "Uploaded_Chapter"
    # Parent immediately above the lesson-id directory is the most reliable
    # chapter folder in the requested input structure.
    for idx, part in enumerate(parts[:-1]):
        if part.strip() == lesson_id and idx > 0:
            return parts[idx - 1].strip() or "Uploaded_Chapter"
    if len(parts) >= 3:
        return parts[-3].strip() or "Uploaded_Chapter"
    if len(parts) >= 2:
        return parts[-2].strip() or "Uploaded_Chapter"
    return "Uploaded_Chapter"


def collect_uploaded_files(uploaded_files) -> tuple[list[FileRecord], list[str], str]:
    """Save Streamlit uploads/ZIP members to a temp folder and return records."""
    records: list[FileRecord] = []
    errors: list[str] = []
    temp_dir = tempfile.mkdtemp(prefix="jove_quiz_expansion_")
    counter = 0

    for uploaded in uploaded_files or []:
        upload_name = getattr(uploaded, "name", "uploaded")
        lower = upload_name.lower()
        if lower.endswith(".zip"):
            try:
                payload = uploaded.read()
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    for member in zf.namelist():
                        parts = _clean_parts(member)
                        if not parts or member.endswith("/"):
                            continue
                        name = parts[-1]
                        ext = Path(name).suffix.lower()
                        if ext not in ALLOWED_EXTENSIONS or name.startswith("~$"):
                            continue
                        relative = "/".join(parts)
                        counter += 1
                        out = os.path.join(temp_dir, f"{counter:04d}_{Path(name).name}")
                        Path(out).write_bytes(zf.read(member))
                        lesson_id = _lesson_id_from_parts(relative)
                        chapter_key = _chapter_key_from_parts(relative, lesson_id)
                        records.append(FileRecord(name, out, relative, ext, lesson_id, chapter_key))
            except zipfile.BadZipFile:
                errors.append(f"{upload_name}: invalid/corrupted ZIP archive.")
            except Exception as exc:
                errors.append(f"{upload_name}: ZIP extraction failed: {exc}")
        else:
            name = Path(upload_name.replace("\\", "/")).name
            ext = Path(name).suffix.lower()
            if ext not in ALLOWED_EXTENSIONS or name.startswith("~$"):
                continue
            try:
                relative = upload_name.replace("\\", "/")
                counter += 1
                out = os.path.join(temp_dir, f"{counter:04d}_{name}")
                data = uploaded.getbuffer() if hasattr(uploaded, "getbuffer") else uploaded.read()
                Path(out).write_bytes(bytes(data))
                lesson_id = _lesson_id_from_parts(relative)
                chapter_key = _chapter_key_from_parts(relative, lesson_id)
                records.append(FileRecord(name, out, relative, ext, lesson_id, chapter_key))
            except Exception as exc:
                errors.append(f"{upload_name}: upload save failed: {exc}")

    return records, errors, temp_dir


def records_from_directory(root: str) -> list[FileRecord]:
    """Offline/test helper: recursively index an extracted input directory."""
    root_path = Path(root)
    records: list[FileRecord] = []
    for p in sorted(root_path.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in ALLOWED_EXTENSIONS or p.name.startswith("~$"):
            continue
        relative = p.relative_to(root_path).as_posix()
        lesson_id = _lesson_id_from_parts(relative)
        chapter_key = _chapter_key_from_parts(relative, lesson_id)
        records.append(FileRecord(p.name, str(p), relative, p.suffix.lower(), lesson_id, chapter_key))
    return records


# -----------------------------------------------------------------------------
# Smart/fuzzy file-role detection
# -----------------------------------------------------------------------------


def _normalized_name(name: str) -> str:
    stem = Path(name).stem
    # Split camel case and separators into words.
    stem = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
    stem = re.sub(r"[^A-Za-z0-9]+", " ", stem).lower()
    return re.sub(r"\s+", " ", stem).strip()


def _contains_token(text: str, token: str) -> bool:
    return bool(re.search(rf"(?:^|\s){re.escape(token)}(?:$|\s)", text))


def _fuzzy_bonus(text: str, targets: Iterable[str]) -> float:
    words = text.split()
    best = 0.0
    for word in words:
        for target in targets:
            ratio = SequenceMatcher(None, word, target).ratio()
            if ratio > best:
                best = ratio
    return max(0.0, (best - 0.70) * 6.0)


def _content_sniff(record: FileRecord) -> dict[str, float]:
    scores = {role: 0.0 for role in ROLE_NAMES}
    if record.extension == ".vtt":
        scores["transcript"] += 10.0
        return scores
    if record.extension == ".txt":
        scores["transcript"] += 4.0
        return scores
    if record.extension != ".docx":
        return scores

    lines, error = read_docx_lines(record.path)
    if error:
        return scores
    sample = "\n".join(lines[:60]).lower()
    if re.search(r"(?:^|\n)\s*\*?[a-d][\)\.\:]\s+", sample):
        scores["quiz"] += 6.0
    if "end-of-lesson quiz" in sample or "end of lesson quiz" in sample:
        scores["quiz"] += 6.0
    if "end-of-chapter quiz" in sample or "end of chapter question" in sample:
        scores["quiz"] += 3.0
    if "lesson name" in sample or "page text" in sample or "pagetext" in sample:
        scores["pagetext"] += 3.0
    if "transcript" in sample or "speaker" in sample:
        scores["transcript"] += 2.0
    return scores


def score_file_roles(record: FileRecord) -> dict[str, float]:
    text = _normalized_name(record.name)
    scores = {role: 0.0 for role in ROLE_NAMES}

    # Extension signals.
    if record.extension == ".vtt":
        scores["transcript"] += 8.0
    elif record.extension == ".txt":
        scores["transcript"] += 2.0
    elif record.extension == ".docx":
        scores["pagetext"] += 0.5
        scores["quiz"] += 0.5
        scores["transcript"] += 0.5

    # Strong filename signals. These are intentionally broad; exact naming is
    # not required. The score combines keywords, fuzzy similarity, extension,
    # and content cues.
    if "pagetext" in text.replace(" ", "") or "page text" in text:
        scores["pagetext"] += 10.0
    if _contains_token(text, "pt"):
        scores["pagetext"] += 7.0
    if _contains_token(text, "ptx") or "long ptx" in text:
        scores["pagetext"] += 5.0
    if "lesson text" in text:
        scores["pagetext"] += 5.0

    if _contains_token(text, "cc"):
        scores["transcript"] += 9.0
    if "closed caption" in text or "closed captions" in text or "caption" in text:
        scores["transcript"] += 8.0
    if "transcript" in text or "transcription" in text:
        scores["transcript"] += 10.0
    if _contains_token(text, "script") or "video script" in text:
        scores["transcript"] += 7.0

    if _contains_token(text, "quiz"):
        scores["quiz"] += 12.0
    if "question bank" in text or "quiz questions" in text:
        scores["quiz"] += 8.0
    if _contains_token(text, "questions") or _contains_token(text, "question"):
        scores["quiz"] += 4.0

    scores["pagetext"] += _fuzzy_bonus(text, ["pagetext", "page", "ptx"])
    scores["transcript"] += _fuzzy_bonus(text, ["transcript", "caption", "script"])
    scores["quiz"] += _fuzzy_bonus(text, ["quiz", "questions"])

    sniff = _content_sniff(record)
    for role in ROLE_NAMES:
        scores[role] += sniff[role]

    record.role_scores = scores
    return scores


def _choose_role(records: list[FileRecord], role: str) -> tuple[FileRecord | None, list[str], list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    candidates: list[tuple[float, FileRecord]] = []
    for record in records:
        scores = score_file_roles(record)
        score = scores[role]
        # Keep only plausible candidates.
        if score >= 4.0:
            candidates.append((score, record))

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    if not candidates:
        errors.append(f"No {role} file detected.")
        return None, warnings, errors

    best_score, best = candidates[0]
    if len(candidates) > 1:
        second_score, second = candidates[1]
        if abs(best_score - second_score) < 1.5:
            errors.append(
                f"Ambiguous {role} files: '{best.name}' ({best_score:.1f}) and "
                f"'{second.name}' ({second_score:.1f}). Rename one or remove the duplicate."
            )
            return None, warnings, errors
        warnings.append(
            f"Multiple {role} candidates found; selected '{best.name}' "
            f"(score {best_score:.1f}) over '{second.name}' ({second_score:.1f})."
        )
    return best, warnings, errors


def bundle_lessons(records: list[FileRecord]) -> list[LessonBundle]:
    grouped: dict[str, list[FileRecord]] = {}
    unassigned: list[FileRecord] = []
    for record in records:
        if record.lesson_id:
            grouped.setdefault(record.lesson_id, []).append(record)
        else:
            unassigned.append(record)

    bundles: list[LessonBundle] = []
    for lesson_id, lesson_records in sorted(grouped.items(), key=lambda kv: int(kv[0])):
        chapter_key = next((r.chapter_key for r in lesson_records if r.chapter_key), "Uploaded_Chapter")
        bundle = LessonBundle(lesson_id=lesson_id, chapter_key=chapter_key)
        for role in ROLE_NAMES:
            selected, warnings, errors = _choose_role(lesson_records, role)
            setattr(bundle, role if role != "pagetext" else "pagetext", selected)
            bundle.warnings.extend(warnings)
            bundle.errors.extend(errors)
        bundles.append(bundle)

    if unassigned:
        # These cannot be safely associated with a lesson; expose this as a
        # synthetic error bundle rather than silently ignoring them.
        bundle = LessonBundle(lesson_id="UNASSIGNED", chapter_key="Uploaded_Chapter")
        bundle.errors.append(
            "Files without a detectable lesson ID were found: " + ", ".join(r.name for r in unassigned[:12])
        )
        bundles.append(bundle)

    return bundles


def load_lesson_sources(bundle: LessonBundle) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    pt_text = ""
    transcript_text = ""

    if bundle.pagetext:
        pt_text, error = read_docx_text(bundle.pagetext.path)
        if error:
            warnings.append(f"PageText read error: {error}")
        if not pt_text:
            warnings.append("PageText contains no readable text.")

    if bundle.transcript:
        transcript_text, error = read_transcript_source(bundle.transcript.path)
        if error:
            warnings.append(f"Transcript/CC read error: {error}")
        if not transcript_text:
            warnings.append("Transcript/CC contains no readable text.")

    return pt_text, transcript_text, warnings
