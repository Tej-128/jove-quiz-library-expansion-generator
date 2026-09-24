from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Callable

from excel_export import build_lesson_workbook, save_workbook
from existing_quiz_parser import parse_existing_quiz_docx
from input_parser import LessonBundle, load_lesson_sources
from quiz_generator import TOTAL_GENERATED, generate_additional_questions

PIPELINE_VERSION = "v1.0.0_quiz_library_expansion"


def _safe_filename(text: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(text or "")).strip("_")
    return value or "quiz"


def process_lesson(
    bundle: LessonBundle,
    *,
    subject: str,
    api_key: str,
    model: str,
    enable_ai_accuracy_qa: bool = True,
    output_dir: str | None = None,
    progress_callback: Callable[[str, int | None], None] | None = None,
) -> tuple[str, dict[str, Any]]:
    if not bundle.ready:
        raise ValueError("Lesson bundle is not ready: " + "; ".join(bundle.errors))
    if not subject.strip():
        raise ValueError("Subject is required.")
    if not api_key.strip():
        raise ValueError("OpenAI API key is required.")

    quiz_result = parse_existing_quiz_docx(bundle.quiz.path, bundle.lesson_id)
    if quiz_result.error:
        raise RuntimeError(f"Existing quiz could not be read: {quiz_result.error}")
    if not quiz_result.questions:
        raise RuntimeError("No existing quiz questions were parsed; generation stopped to avoid losing approved content.")

    pt_text, transcript_text, source_warnings = load_lesson_sources(bundle)
    if not pt_text.strip() and not transcript_text.strip():
        raise RuntimeError("Neither PageText nor Transcript/CC contains readable source text.")

    chapter_name = quiz_result.chapter_name or bundle.chapter_key or "Uploaded Chapter"
    lesson_title = quiz_result.video_title or Path(bundle.quiz.name).stem

    def notify(message: str, pct: int | None = None):
        if progress_callback:
            progress_callback(message, pct)

    notify(f"Generating {TOTAL_GENERATED} new questions for lesson {bundle.lesson_id}...", 35)
    generated, generation_report = generate_additional_questions(
        lesson_id=bundle.lesson_id,
        lesson_title=lesson_title,
        subject=subject.strip(),
        pt_text=pt_text,
        transcript_text=transcript_text,
        existing_questions=quiz_result.questions,
        api_key=api_key,
        model=model,
        enable_ai_accuracy_qa=enable_ai_accuracy_qa,
        progress_callback=progress_callback,
    )

    warnings = []
    warnings.extend(bundle.warnings)
    warnings.extend(quiz_result.warnings)
    warnings.extend(source_warnings)
    warnings.extend(generation_report.get("warnings", []))

    source_files = {
        "Existing Quiz": bundle.quiz.name,
        "PageText": bundle.pagetext.name,
        "Transcript / CC": bundle.transcript.name,
    }

    notify("Building Excel output...", 90)
    wb = build_lesson_workbook(
        subject=subject.strip(),
        chapter_name=chapter_name,
        lesson_id=bundle.lesson_id,
        lesson_title=lesson_title,
        source_files=source_files,
        existing_questions=quiz_result.questions,
        generated_questions=generated,
        warnings=warnings,
    )

    out_dir = output_dir or tempfile.mkdtemp(prefix="jove_quiz_outputs_")
    os.makedirs(out_dir, exist_ok=True)
    title_piece = _safe_filename(lesson_title)[:80]
    output_path = os.path.join(out_dir, f"{bundle.lesson_id}_{title_piece}_Quiz_Expanded.xlsx")
    save_workbook(wb, output_path)

    report = {
        "pipeline_version": PIPELINE_VERSION,
        "lesson_id": bundle.lesson_id,
        "lesson_title": lesson_title,
        "chapter_name": chapter_name,
        "subject": subject.strip(),
        "existing_questions": len(quiz_result.questions),
        "generated_questions": len(generated),
        "total_questions": len(quiz_result.questions) + len(generated),
        "generated_type_counts": generation_report.get("type_counts", {}),
        "generated_review_count": generation_report.get("review_count", 0),
        "generated_fail_count": generation_report.get("fail_count", 0),
        "existing_parse_flags": sum(1 for q in quiz_result.questions if q.get("review_level") in {"review", "fail"}),
        "warnings": warnings,
        "output_file": output_path,
        "source_files": source_files,
    }
    notify("Lesson completed.", 100)
    return output_path, report
