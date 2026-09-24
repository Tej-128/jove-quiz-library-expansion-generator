from __future__ import annotations

from typing import Any

import openpyxl
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

COLUMNS = [
    ("Chapter Name", 30),
    ("Video ID", 13),
    ("Question Index", 14),
    ("Question Content", 58),
    ("Question Type", 20),
    ("Option 1", 44),
    ("Option 2", 44),
    ("Option 3", 44),
    ("Option 4", 44),
    ("Right Answer", 16),
]

HEADER_FILL = PatternFill("solid", fgColor="4472C4")
HEADER_FONT = Font(name="Arial", bold=True, color="FFFFFF", size=10)
BODY_FONT = Font(name="Arial", size=10)
BOLD_FONT = Font(name="Arial", bold=True, size=10)
YELLOW_FILL = PatternFill("solid", fgColor="FFF2CC")
RED_FILL = PatternFill("solid", fgColor="F4CCCC")
GREEN_FILL = PatternFill("solid", fgColor="E2EFDA")
BLUE_FILL = PatternFill("solid", fgColor="DDEBF7")
GRAY_FILL = PatternFill("solid", fgColor="E7E6E6")
THIN_GRAY = Border(
    bottom=Side(style="thin", color="D9E1F2"),
)


def _right_answer_value(question: dict[str, Any]):
    if question.get("question_type") in {"Match the following", "Categorisation"}:
        return None
    value = question.get("right_answer", "")
    return None if value in {None, "", "None", "none"} else str(value)


def _write_headers(ws) -> None:
    for idx, (name, width) in enumerate(COLUMNS, 1):
        cell = ws.cell(1, idx, name)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(idx)].width = width
    ws.row_dimensions[1].height = 28


def _row_fill(question: dict[str, Any]):
    level = str(question.get("review_level", "none")).lower()
    if level == "fail":
        return RED_FILL
    if level == "review":
        return YELLOW_FILL
    return None


def _write_question_row(ws, row: int, question: dict[str, Any], chapter_name: str) -> None:
    values = [
        chapter_name,
        str(question.get("lesson_id", "")),
        question.get("question_index", ""),
        question.get("question_content", ""),
        question.get("question_type", ""),
        question.get("option_1", "") or None,
        question.get("option_2", "") or None,
        question.get("option_3", "") or None,
        question.get("option_4", "") or None,
        _right_answer_value(question),
    ]
    fill = _row_fill(question)
    for col, value in enumerate(values, 1):
        cell = ws.cell(row, col, value)
        cell.font = BODY_FONT
        cell.alignment = Alignment(vertical="top", wrap_text=True)
        cell.border = THIN_GRAY
        if fill:
            cell.fill = fill
    # Approximate a readable auto-fit height because openpyxl cannot invoke Excel's
    # native AutoFit. Long question/option text gets more vertical space.
    widths = [30, 13, 14, 58, 20, 44, 44, 44, 44, 16]
    estimated_lines = 1
    for value, width in zip(values, widths):
        if value is None:
            continue
        text = str(value)
        line_count = sum(max(1, (len(line) // max(8, int(width * 1.35))) + 1) for line in text.splitlines() or [text])
        estimated_lines = max(estimated_lines, line_count)
    ws.row_dimensions[row].height = max(28, min(120, 17 * estimated_lines + 8))

    reason = str(question.get("review_reason", "") or "").strip()
    if reason:
        ws.cell(row, 4).comment = Comment(reason, "JoVE Quiz Generator")


def _write_summary(
    wb: openpyxl.Workbook,
    *,
    subject: str,
    chapter_name: str,
    lesson_id: str,
    lesson_title: str,
    source_files: dict[str, str],
    existing_questions: list[dict[str, Any]],
    generated_questions: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    ws = wb.create_sheet("QA Summary")
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 90

    row = 1
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
    cell = ws.cell(row, 1, "Quiz Library Expansion - QA Summary")
    cell.font = Font(name="Arial", bold=True, color="FFFFFF", size=13)
    cell.fill = HEADER_FILL
    cell.alignment = Alignment(horizontal="center")
    row += 2

    info = [
        ("Subject", subject),
        ("Chapter", chapter_name),
        ("Lesson ID", lesson_id),
        ("Lesson / Video Title", lesson_title),
        ("Existing questions transferred", len(existing_questions)),
        ("New questions generated", len(generated_questions)),
        ("Total quiz rows", len(existing_questions) + len(generated_questions)),
        ("Generated questions needing review", sum(1 for q in generated_questions if q.get("review_level") == "review")),
        ("Generated questions flagged red", sum(1 for q in generated_questions if q.get("review_level") == "fail")),
        ("Existing questions with parse flags", sum(1 for q in existing_questions if q.get("review_level") in {"review", "fail"})),
    ]
    for label, value in info:
        ws.cell(row, 1, label).font = BOLD_FONT
        ws.cell(row, 2, value).font = BODY_FONT
        row += 1

    row += 1
    ws.cell(row, 1, "Source files").font = BOLD_FONT
    row += 1
    for label, value in source_files.items():
        ws.cell(row, 1, label).font = BODY_FONT
        ws.cell(row, 2, value).font = BODY_FONT
        row += 1

    row += 1
    ws.cell(row, 1, "Color legend").font = BOLD_FONT
    row += 1
    ws.cell(row, 1, "No fill")
    ws.cell(row, 2, "Passed automated checks / existing question parsed normally")
    row += 1
    ws.cell(row, 1, "Yellow").fill = YELLOW_FILL
    ws.cell(row, 2, "Manual review recommended")
    row += 1
    ws.cell(row, 1, "Red").fill = RED_FILL
    ws.cell(row, 2, "Critical parse/QA issue - do not approve without checking")
    row += 2

    flagged = [q for q in existing_questions + generated_questions if q.get("review_reason")]
    ws.cell(row, 1, f"Question-level flags ({len(flagged)})").font = BOLD_FONT
    row += 1
    if flagged:
        for q in flagged:
            level = q.get("review_level", "review")
            fill = RED_FILL if level == "fail" else YELLOW_FILL
            ws.cell(row, 1, f"Q{q.get('question_index')} [{q.get('origin', '')}]").fill = fill
            ws.cell(row, 2, q.get("review_reason", "")).fill = fill
            ws.cell(row, 2).alignment = Alignment(wrap_text=True, vertical="top")
            row += 1
    else:
        ws.cell(row, 1, "No question-level flags.").fill = GREEN_FILL
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        row += 1

    if warnings:
        row += 1
        ws.cell(row, 1, f"Run warnings ({len(warnings)})").font = BOLD_FONT
        row += 1
        for warning in warnings:
            ws.cell(row, 1, "Warning").fill = YELLOW_FILL
            ws.cell(row, 2, warning).fill = YELLOW_FILL
            ws.cell(row, 2).alignment = Alignment(wrap_text=True, vertical="top")
            row += 1

    for current_row in range(1, row + 1):
        ws.row_dimensions[current_row].height = 22


def build_lesson_workbook(
    *,
    subject: str,
    chapter_name: str,
    lesson_id: str,
    lesson_title: str,
    source_files: dict[str, str],
    existing_questions: list[dict[str, Any]],
    generated_questions: list[dict[str, Any]],
    warnings: list[str] | None = None,
) -> openpyxl.Workbook:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Quiz"
    ws.freeze_panes = "A2"
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = "1:1"
    _write_headers(ws)

    combined = []
    for q in existing_questions:
        combined.append(dict(q))
    for q in generated_questions:
        combined.append(dict(q))

    # Continuous question indices: existing Word questions first, then all 18 new questions.
    for idx, q in enumerate(combined, 1):
        q["question_index"] = idx
        _write_question_row(ws, idx + 1, q, chapter_name)

    _write_summary(
        wb,
        subject=subject,
        chapter_name=chapter_name,
        lesson_id=lesson_id,
        lesson_title=lesson_title,
        source_files=source_files,
        existing_questions=combined[: len(existing_questions)],
        generated_questions=combined[len(existing_questions) :],
        warnings=warnings or [],
    )
    return wb


def save_workbook(wb: openpyxl.Workbook, path: str) -> None:
    wb.save(path)
