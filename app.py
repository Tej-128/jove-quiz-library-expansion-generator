"""JoVE Quiz Library Expansion Generator - Streamlit app."""
from __future__ import annotations

import csv
import io
import os
import tempfile
import traceback
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from input_parser import bundle_lessons, collect_uploaded_files
from pipeline import PIPELINE_VERSION, process_lesson
from quiz_generator import GENERATED_TYPES, PER_TYPE, TOTAL_GENERATED

st.set_page_config(
    page_title="JoVE Quiz Library Expansion Generator",
    page_icon="JQ",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
<style>
.main-title{font-size:2rem;font-weight:800;color:#1a1a2e;border-bottom:3px solid #E63946;padding-bottom:8px;margin-bottom:4px}
.subtitle{font-size:.95rem;color:#666;margin-bottom:20px}
.stat-box{background:#fff;border-radius:10px;padding:14px 18px;border:1px solid #ddd;box-shadow:0 1px 4px rgba(0,0,0,.06);margin:4px 0}
.stat-label{font-size:.75rem;color:#999;text-transform:uppercase;letter-spacing:.5px}
.stat-value{font-size:1.6rem;font-weight:800;color:#1a1a2e}
</style>
""",
    unsafe_allow_html=True,
)

st.session_state.setdefault("result_zip", None)
st.session_state.setdefault("result_name", "")
st.session_state.setdefault("batch_rows", [])
st.session_state.setdefault("batch_warnings", [])


def _get_api_key() -> str:
    try:
        secret = st.secrets.get("OPENAI_API_KEY", "")
    except Exception:
        secret = ""
    return str(secret or os.environ.get("OPENAI_API_KEY", "")).strip()


def _safe_name(value: str) -> str:
    import re
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_")
    return cleaned or "Quiz_Expansion"


def _build_zip(output_files: list[tuple[str, str]], batch_rows: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for archive_path, real_path in output_files:
            zf.write(real_path, archive_path)
        csv_buf = io.StringIO()
        if batch_rows:
            fieldnames = list(batch_rows[0].keys())
            writer = csv.DictWriter(csv_buf, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(batch_rows)
        zf.writestr("generation_summary.csv", csv_buf.getvalue())
    return buf.getvalue()


api_key = _get_api_key()

with st.sidebar:
    st.markdown("## Configuration")
    if api_key:
        st.success("OpenAI API key configured for this app.")
    else:
        st.error("OPENAI_API_KEY is not configured in Streamlit Secrets or environment variables.")

    subject = st.text_input(
        "Subject",
        placeholder="e.g. Finance, Business, Biology, Chemistry",
        help="Subject provides context only. Uploaded PageText + Transcript/CC remain the source of truth.",
    )

    model = st.selectbox(
        "Model",
        ["gpt-5.5", "gpt-4.1", "gpt-4o"],
        index=0,
        help="Use a strong model for source-grounded question generation and QA.",
    )

    enable_ai_accuracy_qa = st.checkbox(
        "Run AI accuracy QA",
        value=True,
        help="Reviews only the newly generated questions against the uploaded source. Existing Word questions are never rewritten or re-solved.",
    )

    st.markdown("---")
    st.markdown(
        f"""
**Locked output rules**
- Existing Word questions are transferred as-is
- `*` in existing options defines the correct answer(s)
- {TOTAL_GENERATED} new questions per lesson
- {PER_TYPE} each: {', '.join(GENERATED_TYPES)}
- One Excel per lesson
- Existing questions first; new questions appended underneath
- Yellow rows = manual review recommended
- Red rows = critical review required
"""
    )
    st.caption(f"JoVE Internal Tool - {PIPELINE_VERSION}")

st.markdown('<div class="main-title">JoVE Quiz Library Expansion Generator</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Convert existing lesson quiz Word documents to the Quiz Library Excel format, then append 18 source-grounded new questions.</div>',
    unsafe_allow_html=True,
)

uploaded_files = st.file_uploader(
    "Upload chapter ZIP(s) or lesson source files",
    type=["zip", "docx", "vtt", "txt"],
    accept_multiple_files=True,
    help="Recommended structure: Chapter folder > Lesson ID folder > PageText + Transcript/CC + existing Quiz Word document.",
)

if not uploaded_files:
    st.info("Upload a chapter ZIP to begin. The detector uses file names, extensions, folder IDs, fuzzy matching, and content clues rather than one rigid naming convention.")
    st.stop()

records, upload_errors, temp_dir = collect_uploaded_files(uploaded_files)
bundles = bundle_lessons(records)

if upload_errors:
    for error in upload_errors:
        st.error(error)

preview_rows = []
for bundle in bundles:
    preview_rows.append({
        "Lesson ID": bundle.lesson_id,
        "Chapter Folder": bundle.chapter_key,
        "PageText": bundle.pagetext.name if bundle.pagetext else "NOT DETECTED",
        "Transcript / CC": bundle.transcript.name if bundle.transcript else "NOT DETECTED",
        "Existing Quiz": bundle.quiz.name if bundle.quiz else "NOT DETECTED",
        "Status": "Ready" if bundle.ready else "Needs input review",
        "Warnings / Errors": " | ".join(bundle.errors + bundle.warnings),
    })

st.markdown("### Detected lessons")
st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)

ready = [bundle for bundle in bundles if bundle.ready]
issues = [bundle for bundle in bundles if not bundle.ready]

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("Lessons detected", len([b for b in bundles if b.lesson_id != "UNASSIGNED"]))
with c2:
    st.metric("Ready to generate", len(ready))
with c3:
    st.metric("Expected new questions", len(ready) * TOTAL_GENERATED)

if issues:
    st.warning(f"{len(issues)} lesson bundle(s) require input review. They will not be generated until the file-role ambiguity/missing file is fixed.")

if not subject.strip():
    st.warning("Enter the Subject in the sidebar before generation.")

if not api_key:
    st.warning("Configure OPENAI_API_KEY before generation.")

st.markdown("---")
st.markdown("### Generate batch")

if st.button(
    "Generate Expanded Lesson Quizzes",
    type="primary",
    use_container_width=True,
    disabled=(not ready or not subject.strip() or not api_key),
):
    progress = st.progress(0)
    status_box = st.empty()
    output_dir = tempfile.mkdtemp(prefix="jove_quiz_expansion_outputs_")
    output_files: list[tuple[str, str]] = []
    batch_rows: list[dict] = []
    batch_warnings: list[str] = []

    for idx, bundle in enumerate(ready, 1):
        base_pct = int(((idx - 1) / max(1, len(ready))) * 100)
        status_box.info(f"Processing lesson {bundle.lesson_id} ({idx}/{len(ready)})")

        def lesson_progress(message, pct=None):
            status_box.info(f"Lesson {bundle.lesson_id}: {message}")
            if pct is not None:
                within = pct / 100.0
                overall = int((((idx - 1) + within) / max(1, len(ready))) * 100)
                progress.progress(min(100, max(0, overall)))

        try:
            output_path, report = process_lesson(
                bundle,
                subject=subject,
                api_key=api_key,
                model=model,
                enable_ai_accuracy_qa=enable_ai_accuracy_qa,
                output_dir=output_dir,
                progress_callback=lesson_progress,
            )
            archive_path = f"{_safe_name(bundle.chapter_key)}/{bundle.lesson_id}/{Path(output_path).name}"
            output_files.append((archive_path, output_path))
            batch_rows.append({
                "Lesson ID": report["lesson_id"],
                "Lesson Title": report["lesson_title"],
                "Chapter": report["chapter_name"],
                "Existing Questions": report["existing_questions"],
                "New Questions": report["generated_questions"],
                "Total Questions": report["total_questions"],
                "New Review Flags": report["generated_review_count"],
                "New Red Flags": report["generated_fail_count"],
                "Existing Parse Flags": report["existing_parse_flags"],
                "Status": "Completed",
            })
            batch_warnings.extend(f"Lesson {bundle.lesson_id}: {w}" for w in report.get("warnings", []))
        except Exception as exc:
            batch_rows.append({
                "Lesson ID": bundle.lesson_id,
                "Lesson Title": "",
                "Chapter": bundle.chapter_key,
                "Existing Questions": "",
                "New Questions": "",
                "Total Questions": "",
                "New Review Flags": "",
                "New Red Flags": "",
                "Existing Parse Flags": "",
                "Status": f"Failed: {exc}",
            })
            batch_warnings.append(f"Lesson {bundle.lesson_id} failed: {exc}")
            batch_warnings.append(traceback.format_exc(limit=3))

        progress.progress(min(100, int((idx / max(1, len(ready))) * 100)))

    if output_files:
        zip_bytes = _build_zip(output_files, batch_rows)
        st.session_state["result_zip"] = zip_bytes
        st.session_state["result_name"] = f"{_safe_name(subject)}_Quiz_Library_Expansion.zip"
        st.session_state["batch_rows"] = batch_rows
        st.session_state["batch_warnings"] = batch_warnings
        status_box.success(f"Completed {len(output_files)} lesson Excel file(s).")
    else:
        status_box.error("No lesson Excel files were created.")
        st.session_state["result_zip"] = None
        st.session_state["batch_rows"] = batch_rows
        st.session_state["batch_warnings"] = batch_warnings

if st.session_state.get("batch_rows"):
    st.markdown("### Batch results")
    st.dataframe(pd.DataFrame(st.session_state["batch_rows"]), use_container_width=True, hide_index=True)

if st.session_state.get("batch_warnings"):
    with st.expander(f"Warnings / diagnostics ({len(st.session_state['batch_warnings'])})"):
        for warning in st.session_state["batch_warnings"]:
            st.write(warning)

if st.session_state.get("result_zip"):
    st.download_button(
        "Download Updated Quiz Excel ZIP",
        data=st.session_state["result_zip"],
        file_name=st.session_state.get("result_name") or "Quiz_Library_Expansion.zip",
        mime="application/zip",
        use_container_width=True,
    )
