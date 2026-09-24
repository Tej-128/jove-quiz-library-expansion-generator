from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from input_parser import read_docx_lines

OPTION_RE = re.compile(r"^\s*(\*)?\s*([A-Da-d])\s*[\)\.\:\-]\s*(\*)?\s*(.+?)\s*$")
NUMBERED_QUESTION_RE = re.compile(r"^\s*\d+\s*[\)\.]\s*(.+?)\s*$")

META_PATTERNS = {
    "chapter_name": re.compile(r"^\s*chapter\s*(?:title|name)?\s*[:\-]\s*(.+?)\s*$", re.I),
    "video_title": re.compile(r"^\s*video\s*title\s*[:\-]\s*(.+?)\s*$", re.I),
    "writer": re.compile(r"^\s*(?:writer|author|reviewer)\s*[:\-]\s*(.+?)\s*$", re.I),
}

# These are document scaffolding, never question content. The question immediately
# under an End-of-Chapter heading is still parsed normally.
SECTION_HEADING_RE = re.compile(
    r"^\s*(?:"
    r"end[\s\-]*of[\s\-]*(?:lesson|chapter)\s*(?:quiz)?(?:\s*question)?s?"
    r"|quiz\s*questions?"
    r"|end[\s\-]*of[\s\-]*lesson\s*quiz"
    r"|easy\s*questions?"
    r"|medium\s*questions?"
    r"|hard\s*questions?"
    r"|additional\s*questions?"
    r")\s*:?\s*$",
    re.I,
)


@dataclass
class ExistingQuizParseResult:
    questions: list[dict[str, Any]] = field(default_factory=list)
    chapter_name: str = ""
    video_title: str = ""
    writer: str = ""
    warnings: list[str] = field(default_factory=list)
    error: str = ""


def _strip_question_number(text: str) -> str:
    match = NUMBERED_QUESTION_RE.match(text)
    return match.group(1) if match else text.strip()


def _is_metadata_or_heading(line: str) -> tuple[bool, str, str]:
    for key, pattern in META_PATTERNS.items():
        match = pattern.match(line)
        if match:
            return True, key, match.group(1).strip()
    if SECTION_HEADING_RE.match(line):
        return True, "heading", ""
    return False, "", ""


def _parse_option(line: str):
    match = OPTION_RE.match(line)
    if not match:
        return None
    starred = bool(match.group(1) or match.group(3))
    letter = match.group(2).upper()
    content = match.group(4)
    return starred, letter, content


def _finalize_question(
    question_text: str,
    options: list[tuple[bool, str, str]],
    lesson_id: str,
    index: int,
) -> dict[str, Any]:
    # Preserve wording exactly except for structural Word markers: question number,
    # option label, and '*' answer indicator. Option order remains unchanged.
    option_values = [opt[2] for opt in options[:4]]
    while len(option_values) < 4:
        option_values.append("")

    starred_positions = [str(i + 1) for i, opt in enumerate(options[:4]) if opt[0]]
    if len(starred_positions) == 1:
        question_type = "Single Correct"
        review_level = "none"
        review_reason = ""
    elif len(starred_positions) >= 2:
        question_type = "Multi Correct"
        review_level = "none"
        review_reason = ""
    else:
        # We do not guess an answer. Keep the question in output and flag it.
        question_type = "Single Correct"
        review_level = "fail"
        review_reason = "Existing question has no *-marked correct answer."

    if len(options) != 4:
        review_level = "fail"
        extra = f"Existing question has {len(options)} parsed option(s); expected 4."
        review_reason = (review_reason + " " + extra).strip()

    # Preserve option labels semantically by requiring A-D in sequence. Do not
    # reorder or repair out-of-order source questions.
    letters = [opt[1] for opt in options]
    expected = ["A", "B", "C", "D"][: len(options)]
    if letters != expected:
        review_level = "fail"
        extra = f"Option labels/order are {letters}; expected {expected}."
        review_reason = (review_reason + " " + extra).strip()

    return {
        "lesson_id": lesson_id,
        "question_index": index,
        "question_content": _strip_question_number(question_text),
        "question_type": question_type,
        "option_1": option_values[0],
        "option_2": option_values[1],
        "option_3": option_values[2],
        "option_4": option_values[3],
        "right_answer": ",".join(starred_positions),
        "origin": "existing",
        "review_level": review_level,
        "review_reason": review_reason,
    }


def parse_existing_quiz_docx(path: str, lesson_id: str) -> ExistingQuizParseResult:
    result = ExistingQuizParseResult()
    lines, error = read_docx_lines(path)
    if error:
        result.error = error
        return result

    # Extract metadata without carrying it into quiz rows.
    content_lines: list[str] = []
    for line in lines:
        is_meta, key, value = _is_metadata_or_heading(line)
        if is_meta:
            if key == "chapter_name" and not result.chapter_name:
                result.chapter_name = value
            elif key == "video_title" and not result.video_title:
                result.video_title = value
            elif key == "writer" and not result.writer:
                result.writer = value
            continue
        content_lines.append(line)

    i = 0
    while i < len(content_lines):
        line = content_lines[i]
        if _parse_option(line):
            # Orphan option: retain warning; never invent a question stem.
            result.warnings.append(f"Orphan option ignored near line: {line[:120]}")
            i += 1
            continue

        # A valid existing quiz question is a non-option line followed by a run of
        # A-D option paragraphs. This avoids accidentally importing document prose.
        options: list[tuple[bool, str, str]] = []
        j = i + 1
        while j < len(content_lines) and len(options) < 6:
            parsed = _parse_option(content_lines[j])
            if not parsed:
                break
            options.append(parsed)
            j += 1

        if options:
            question = _finalize_question(line, options, lesson_id, len(result.questions) + 1)
            result.questions.append(question)
            i = j
            continue

        # Non-question prose is intentionally not transferred.
        i += 1

    if not result.questions:
        result.warnings.append("No existing quiz questions were parsed from the Word document.")
    return result
