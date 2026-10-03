"""JoVE Quiz Library Expansion Generator - Streamlit app."""
from __future__ import annotations

import csv
import hmac
import io
import queue
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import os
import shutil
import tempfile
import traceback
import time
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
:root{--jove-red:#d71920;--jove-red-dark:#a9151a;--jove-ink:#20242a;--jove-muted:#67707a;--jove-soft:#f5f6f7;--jove-border:#e4e7eb}
.stApp{background:#ffffff;color:var(--jove-ink)}
[data-testid="stSidebar"]{background:#f7f7f8;border-right:1px solid var(--jove-border)}
[data-testid="stSidebar"] h1,[data-testid="stSidebar"] h2,[data-testid="stSidebar"] h3{color:var(--jove-ink)}
[data-testid="stMetric"]{background:#ffffff;border:1px solid var(--jove-border);border-radius:10px;padding:14px 16px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
[data-testid="stFileUploader"] section{border:1.5px dashed #c9cdd2;border-radius:10px;background:#fafafa}
[data-testid="stFileUploader"] section:hover{border-color:var(--jove-red)}
.stButton>button,.stDownloadButton>button{border-radius:7px;font-weight:700}
.stButton>button[kind="primary"],.stDownloadButton>button{background:var(--jove-red);border-color:var(--jove-red);color:white}
.stButton>button[kind="primary"]:hover,.stDownloadButton>button:hover{background:var(--jove-red-dark);border-color:var(--jove-red-dark);color:white}
.jove-header{display:flex;align-items:center;justify-content:space-between;padding:18px 22px;border:1px solid var(--jove-border);border-left:6px solid var(--jove-red);border-radius:10px;background:#fff;margin-bottom:20px}
.jove-brand{font-size:2.05rem;font-weight:900;letter-spacing:-1px;color:var(--jove-red);line-height:1}
.jove-product{font-size:1.15rem;font-weight:750;color:var(--jove-ink);margin-top:5px}
.jove-subtitle{font-size:.92rem;color:var(--jove-muted);margin-top:5px}
.jove-badge{font-size:.72rem;font-weight:800;letter-spacing:.6px;text-transform:uppercase;background:#fcebec;color:var(--jove-red-dark);border:1px solid #f3c4c7;border-radius:999px;padding:7px 11px}
.auth-wrap{max-width:560px;margin:8vh auto 0 auto;padding:28px 30px;border:1px solid var(--jove-border);border-top:5px solid var(--jove-red);border-radius:12px;background:#fff;box-shadow:0 8px 30px rgba(0,0,0,.06)}
.auth-brand{font-size:2.2rem;font-weight:900;color:var(--jove-red);letter-spacing:-1px}
.auth-title{font-size:1.35rem;font-weight:800;color:var(--jove-ink);margin-top:7px}
.auth-copy{font-size:.92rem;color:var(--jove-muted);margin:8px 0 18px 0}
.section-kicker{font-size:.72rem;color:var(--jove-red);font-weight:800;letter-spacing:.8px;text-transform:uppercase;margin-bottom:3px}
hr{border:none;border-top:1px solid var(--jove-border)}
</style>
""",
    unsafe_allow_html=True,
)

st.session_state.setdefault("result_zip", None)
st.session_state.setdefault("result_name", "")
st.session_state.setdefault("result_parts", [])
st.session_state.setdefault("batch_rows", [])
st.session_state.setdefault("batch_warnings", [])
st.session_state.setdefault("jove_access_granted", False)
st.session_state.setdefault("active_upload_temp_dir", "")


def _get_api_key() -> str:
    try:
        secret = st.secrets.get("OPENAI_API_KEY", "")
    except Exception:
        secret = ""
    return str(secret or os.environ.get("OPENAI_API_KEY", "")).strip()


def _get_access_password() -> str:
    """Read the app-level access password without storing it in public GitHub code."""
    try:
        secret = st.secrets.get("APP_ACCESS_PASSWORD", "")
    except Exception:
        secret = ""
    return str(secret or os.environ.get("APP_ACCESS_PASSWORD", "")).strip()


def _require_app_access() -> None:
    """Fail closed until the user enters the shared JoVE pilot password."""
    configured_password = _get_access_password()
    if st.session_state.get("jove_access_granted"):
        return

    st.markdown(
        """
        <div class="auth-wrap">
          <div class="auth-brand">JoVE</div>
          <div class="auth-title">Quiz Library Expansion</div>
          <div class="auth-copy">Internal pilot workspace. Enter the access password to continue.</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not configured_password:
        st.error("APP_ACCESS_PASSWORD is not configured. Add it in the app's Secrets before using this public deployment.")
        st.stop()

    entered_password = st.text_input(
        "Access password",
        type="password",
        placeholder="Enter JoVE pilot password",
        key="jove_access_password_input",
    )
    if st.button("Enter secure workspace", type="primary", use_container_width=True):
        if hmac.compare_digest(entered_password, configured_password):
            st.session_state["jove_access_granted"] = True
            st.session_state.pop("jove_access_password_input", None)
            st.rerun()
        else:
            st.error("Incorrect access password.")
    st.stop()


_require_app_access()


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
    st.markdown('<div class="section-kicker">JoVE Internal</div>', unsafe_allow_html=True)
    st.markdown("## Quiz Expansion Setup")
    if st.button("Lock workspace", use_container_width=True):
        st.session_state["jove_access_granted"] = False
        st.rerun()
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

    batch_workers = int(st.number_input(
        "Parallel lesson workers",
        min_value=1,
        max_value=6,
        value=4,
        step=1,
        help="Large-batch mode. Four workers is the default; lower this only if your API account is rate-limited.",
    ))

    st.markdown("---")
    st.markdown(
        f"""
**Locked output rules**
- Existing Word wording/order is preserved; explicit equations receive the required readable + LaTeX representation
- `*` in existing options defines the correct answer(s)
- {TOTAL_GENERATED} new questions per lesson
- {PER_TYPE} each: {', '.join(GENERATED_TYPES)}
- One Excel per lesson
- Existing and new questions are separated by labeled/color-coded sections in Excel
- Yellow rows = manual review recommended
- Red rows = critical review required
"""
    )
    st.caption(f"JoVE Internal Tool - {PIPELINE_VERSION}")

st.markdown(
    """
    <div class="jove-header">
      <div>
        <div class="jove-brand">JoVE</div>
        <div class="jove-product">Quiz Library Expansion Generator</div>
        <div class="jove-subtitle">Convert approved lesson quizzes to Excel and append 21 source-grounded questions across 7 question types.</div>
      </div>
      <div class="jove-badge">Internal Pilot</div>
    </div>
    """,
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

previous_temp_dir = st.session_state.get("active_upload_temp_dir", "")
if previous_temp_dir and os.path.isdir(previous_temp_dir):
    shutil.rmtree(previous_temp_dir, ignore_errors=True)
records, upload_errors, temp_dir = collect_uploaded_files(uploaded_files)
st.session_state["active_upload_temp_dir"] = temp_dir
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

if len(ready) >= 50:
    st.info(
        f"Large-batch mode enabled for {len(ready)} ready lessons. "
        f"The app will process up to {batch_workers} lessons in parallel and create a checkpoint ZIP part after every 25 successful lessons."
    )

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
    live_results = st.empty()
    heartbeat_box = st.empty()
    output_dir = tempfile.mkdtemp(prefix="jove_quiz_expansion_outputs_")
    output_files: list[tuple[str, str]] = []
    batch_rows_by_index: dict[int, dict] = {}
    batch_warnings: list[str] = []
    batch_started = time.monotonic()
    progress_events: queue.Queue = queue.Queue()
    checkpoint_outputs: list[tuple[str, str]] = []
    checkpoint_rows: list[dict] = []
    checkpoint_part_number = 0
    CHECKPOINT_PART_SIZE = 25

    st.session_state["result_zip"] = None
    st.session_state["result_name"] = ""
    st.session_state["result_parts"] = []
    st.session_state["batch_rows"] = []
    st.session_state["batch_warnings"] = []

    def _run_one_lesson(index: int, bundle):
        lesson_started = time.monotonic()

        def lesson_progress(message, pct=None):
            progress_events.put(
                {
                    "index": index,
                    "lesson_id": bundle.lesson_id,
                    "message": message,
                    "pct": pct,
                    "elapsed": int(time.monotonic() - lesson_started),
                }
            )

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
            row = {
                "Lesson ID": report["lesson_id"],
                "Lesson Title": report["lesson_title"],
                "Chapter": report["chapter_name"],
                "Existing Questions": report["existing_questions"],
                "New Questions": report["generated_questions"],
                "Total Questions": report["total_questions"],
                "New Review Flags": report["generated_review_count"],
                "New Red Flags": report["generated_fail_count"],
                "QA Replacements": report.get("qa_replacements", 0),
                "QA Repair Rounds": report.get("qa_repair_rounds", 0),
                "Existing Parse Flags": report["existing_parse_flags"],
                "Elapsed (s)": int(time.monotonic() - lesson_started),
                "Status": "Completed",
            }
            return {
                "index": index,
                "success": True,
                "row": row,
                "output": (archive_path, output_path),
                "warnings": [f"Lesson {bundle.lesson_id}: {w}" for w in report.get("warnings", [])],
            }
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            row = {
                "Lesson ID": bundle.lesson_id,
                "Lesson Title": "",
                "Chapter": bundle.chapter_key,
                "Existing Questions": "",
                "New Questions": "",
                "Total Questions": "",
                "New Review Flags": "",
                "New Red Flags": "",
                "QA Replacements": "",
                "QA Repair Rounds": "",
                "Existing Parse Flags": "",
                "Elapsed (s)": int(time.monotonic() - lesson_started),
                "Status": f"Failed: {error_text}",
            }
            return {
                "index": index,
                "success": False,
                "row": row,
                "output": None,
                "warnings": [
                    f"Lesson {bundle.lesson_id} failed: {error_text}",
                    traceback.format_exc(limit=8),
                ],
            }

    try:
        worker_count = max(1, min(int(batch_workers), 6, len(ready)))
        status_box.info(
            f"Starting {len(ready)} lessons with {worker_count} parallel worker(s)..."
        )

        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix="jove-quiz",
        ) as executor:
            future_map = {
                executor.submit(_run_one_lesson, index, bundle): (index, bundle)
                for index, bundle in enumerate(ready, 1)
            }
            pending = set(future_map)
            completed = 0

            while pending:
                done, pending = wait(
                    pending,
                    timeout=0.75,
                    return_when=FIRST_COMPLETED,
                )

                latest_event = None
                while True:
                    try:
                        latest_event = progress_events.get_nowait()
                    except queue.Empty:
                        break
                if latest_event:
                    heartbeat_box.caption(
                        f"Last activity: lesson {latest_event['lesson_id']} - "
                        f"{latest_event['message']} - lesson elapsed {latest_event['elapsed']}s | "
                        f"batch elapsed {int(time.monotonic() - batch_started)}s"
                    )

                for future in done:
                    index, bundle = future_map[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        error_text = f"{type(exc).__name__}: {exc}"
                        result = {
                            "index": index,
                            "success": False,
                            "row": {
                                "Lesson ID": bundle.lesson_id,
                                "Lesson Title": "",
                                "Chapter": bundle.chapter_key,
                                "Existing Questions": "",
                                "New Questions": "",
                                "Total Questions": "",
                                "New Review Flags": "",
                                "New Red Flags": "",
                                "QA Replacements": "",
                                "QA Repair Rounds": "",
                                "Existing Parse Flags": "",
                                "Elapsed (s)": "",
                                "Status": f"Failed: {error_text}",
                            },
                            "output": None,
                            "warnings": [
                                f"Lesson {bundle.lesson_id} worker failed: {error_text}",
                                traceback.format_exc(limit=8),
                            ],
                        }

                    completed += 1
                    batch_rows_by_index[result["index"]] = result["row"]
                    checkpoint_rows.append(result["row"])
                    batch_warnings.extend(result.get("warnings", []))

                    if result.get("success") and result.get("output"):
                        output_files.append(result["output"])
                        checkpoint_outputs.append(result["output"])
                        status_box.success(
                            f"Completed lesson {result['row']['Lesson ID']} "
                            f"({completed}/{len(ready)} finished)."
                        )
                    else:
                        status_box.error(
                            f"Lesson {result['row']['Lesson ID']} failed "
                            f"({completed}/{len(ready)} finished). Continuing the batch."
                        )

                    progress.progress(
                        min(100, max(0, int((completed / max(1, len(ready))) * 100)))
                    )

                    ordered_rows = [
                        batch_rows_by_index[key]
                        for key in sorted(batch_rows_by_index)
                    ]
                    st.session_state["batch_rows"] = ordered_rows
                    st.session_state["batch_warnings"] = list(batch_warnings)
                    # Limit repeated browser rendering cost during 500-lesson runs.
                    live_results.dataframe(
                        pd.DataFrame(ordered_rows[-200:]),
                        use_container_width=True,
                        hide_index=True,
                    )

                    if len(checkpoint_outputs) >= CHECKPOINT_PART_SIZE:
                        checkpoint_part_number += 1
                        part_name = (
                            f"{_safe_name(subject)}_Quiz_Library_Expansion_"
                            f"Part_{checkpoint_part_number:02d}.zip"
                        )
                        part_bytes = _build_zip(
                            list(checkpoint_outputs),
                            list(checkpoint_rows),
                        )
                        st.session_state["result_parts"].append(
                            {"name": part_name, "data": part_bytes}
                        )
                        checkpoint_outputs = []
                        checkpoint_rows = []

        ordered_rows = [
            batch_rows_by_index[key]
            for key in sorted(batch_rows_by_index)
        ]

        # Flush the final partial checkpoint group, if any.
        if checkpoint_outputs:
            checkpoint_part_number += 1
            part_name = (
                f"{_safe_name(subject)}_Quiz_Library_Expansion_"
                f"Part_{checkpoint_part_number:02d}.zip"
            )
            part_bytes = _build_zip(
                list(checkpoint_outputs),
                list(checkpoint_rows),
            )
            st.session_state["result_parts"].append(
                {"name": part_name, "data": part_bytes}
            )

        if output_files:
            # Build the complete ZIP once. Earlier versions rebuilt an ever-growing
            # ZIP after every lesson, which is unnecessarily expensive for 100-500 lessons.
            zip_bytes = _build_zip(output_files, ordered_rows)
            st.session_state["result_zip"] = zip_bytes
            st.session_state["result_name"] = (
                f"{_safe_name(subject)}_Quiz_Library_Expansion.zip"
            )
            st.session_state["batch_rows"] = ordered_rows
            st.session_state["batch_warnings"] = batch_warnings
            status_box.success(
                f"Completed {len(output_files)} lesson Excel file(s) out of "
                f"{len(ready)} in {int(time.monotonic() - batch_started)}s."
            )
        else:
            status_box.error(
                "No lesson Excel files were created. Review the live results and diagnostics below."
            )
            st.session_state["result_zip"] = None
            st.session_state["batch_rows"] = ordered_rows
            st.session_state["batch_warnings"] = batch_warnings

    except Exception as batch_exc:
        error_text = f"{type(batch_exc).__name__}: {batch_exc}"
        batch_warnings.append(f"Batch orchestration failed: {error_text}")
        batch_warnings.append(traceback.format_exc(limit=12))
        ordered_rows = [
            batch_rows_by_index[key]
            for key in sorted(batch_rows_by_index)
        ]
        st.session_state["batch_rows"] = ordered_rows
        st.session_state["batch_warnings"] = batch_warnings

        if output_files:
            try:
                st.session_state["result_zip"] = _build_zip(
                    output_files,
                    ordered_rows,
                )
                st.session_state["result_name"] = (
                    f"{_safe_name(subject)}_Quiz_Library_Expansion_PARTIAL.zip"
                )
            except Exception as checkpoint_exc:
                batch_warnings.append(
                    f"Partial ZIP recovery failed: {checkpoint_exc}"
                )

        status_box.error(
            f"Batch stopped unexpectedly: {error_text}. "
            "Completed outputs and checkpoint parts were preserved where possible."
        )

    finally:
        shutil.rmtree(output_dir, ignore_errors=True)
        if temp_dir and os.path.isdir(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
        st.session_state["active_upload_temp_dir"] = ""

if st.session_state.get("batch_rows"):
    st.markdown("### Batch results")
    st.dataframe(pd.DataFrame(st.session_state["batch_rows"]), use_container_width=True, hide_index=True)

if st.session_state.get("batch_warnings"):
    with st.expander(f"Warnings / diagnostics ({len(st.session_state['batch_warnings'])})"):
        for warning in st.session_state["batch_warnings"]:
            st.write(warning)

if st.session_state.get("result_parts"):
    with st.expander(f"Checkpoint ZIP parts ({len(st.session_state['result_parts'])})"):
        st.caption("Useful for very large batches; each part contains up to 25 completed lesson workbooks.")
        for part_index, part in enumerate(st.session_state["result_parts"], 1):
            st.download_button(
                f"Download checkpoint part {part_index:02d}",
                data=part["data"],
                file_name=part["name"],
                mime="application/zip",
                key=f"checkpoint_download_{part_index}",
                use_container_width=True,
            )

if st.session_state.get("result_zip"):
    st.download_button(
        "Download Updated Quiz Excel ZIP",
        data=st.session_state["result_zip"],
        file_name=st.session_state.get("result_name") or "Quiz_Library_Expansion.zip",
        mime="application/zip",
        use_container_width=True,
    )
