"""JoVE Quiz Library Expansion Generator - hosted Streamlit production batch."""
from __future__ import annotations

import base64
import gc
import io
import os
import shutil
import tempfile
import time
import traceback
import zipfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from input_parser import bundle_lessons, collect_uploaded_files
from pipeline import PIPELINE_VERSION, process_lesson
from quiz_generator import GENERATED_TYPES, PER_TYPE, TOTAL_GENERATED

APP_BUILD = "v1.8.0_streamlit_autodownload"
EXPECTED_TOTAL_GENERATED = 21

st.set_page_config(
    page_title="JoVE Quiz Library Expansion Generator",
    page_icon="JQ",
    layout="wide",
    initial_sidebar_state="expanded",
)

if TOTAL_GENERATED != EXPECTED_TOTAL_GENERATED:
    st.error(
        f"Build mismatch: this app requires {EXPECTED_TOTAL_GENERATED} generated "
        f"questions per lesson, but the loaded generator reports {TOTAL_GENERATED}."
    )
    st.stop()

st.markdown(
    """
<style>
:root{--jove-red:#d71920;--jove-red-dark:#a9151a;--ink:#20242a;--muted:#68717a;--border:#e3e6ea}
.stApp{background:#fff;color:var(--ink)}
[data-testid="stSidebar"]{background:#f7f7f8;border-right:1px solid var(--border)}
[data-testid="stMetric"]{background:#fff;border:1px solid var(--border);border-radius:10px;padding:12px 14px}
[data-testid="stFileUploader"] section{border:1.5px dashed #c9cdd2;border-radius:10px;background:#fafafa}
.stButton>button,.stDownloadButton>button{border-radius:7px;font-weight:700}
.stButton>button[kind="primary"]{background:var(--jove-red);border-color:var(--jove-red);color:#fff}
.stButton>button[kind="primary"]:hover{background:var(--jove-red-dark);border-color:var(--jove-red-dark)}
.jove-header{padding:18px 22px;border:1px solid var(--border);border-left:6px solid var(--jove-red);border-radius:10px;margin-bottom:20px}
.jove-brand{font-size:2rem;font-weight:900;color:var(--jove-red)}
.jove-title{font-size:1.18rem;font-weight:800;color:var(--ink);margin-top:4px}
.jove-sub{font-size:.91rem;color:var(--muted);margin-top:5px}
.ok{background:#edf8ef;border-left:4px solid #2e7d32;padding:9px 12px;border-radius:4px}
.warn{background:#fff8e1;border-left:4px solid #f9a825;padding:9px 12px;border-radius:4px}
</style>
""",
    unsafe_allow_html=True,
)

st.session_state.setdefault("batch_result", None)
st.session_state.setdefault("upload_signature", None)

TEAM_OPENAI_API_KEY = ""


def _get_api_key() -> str:
    if TEAM_OPENAI_API_KEY.strip():
        return TEAM_OPENAI_API_KEY.strip()
    try:
        secret = st.secrets.get("OPENAI_API_KEY", "")
    except Exception:
        secret = ""
    return str(secret or os.environ.get("OPENAI_API_KEY", "")).strip()


def _safe_filename(value: str) -> str:
    import re

    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "")).strip("_")
    return cleaned or "output"


def _auto_download_bytes(data: bytes, file_name: str, mime: str, nonce: str) -> None:
    """Use the same browser auto-download pattern as the original JoVE Quiz Generator."""
    if not data:
        return
    safe_name = (
        file_name.replace("\\", "_")
        .replace("/", "_")
        .replace('"', "_")
    )
    encoded = base64.b64encode(data).decode("ascii")
    components.html(
        f"""
        <html>
          <body>
            <script>
              (function() {{
                const marker = "jove-auto-download-{nonce}";
                if (window[marker]) {{ return; }}
                window[marker] = true;
                const link = document.createElement("a");
                link.href = "data:{mime};base64,{encoded}";
                link.download = "{safe_name}";
                link.style.display = "none";
                document.body.appendChild(link);
                link.click();
                setTimeout(function() {{
                  document.body.removeChild(link);
                }}, 500);
              }})();
            </script>
          </body>
        </html>
        """,
        height=0,
    )


def _build_zip_bytes(
    output_files: list[tuple[str, str]],
    summaries: list[dict[str, Any]],
    errors: list[str],
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for archive_path, disk_path in output_files:
            if os.path.exists(disk_path):
                zf.write(disk_path, arcname=archive_path)
        if summaries:
            summary_csv = pd.DataFrame(summaries).to_csv(index=False)
            zf.writestr("generation_summary.csv", summary_csv)
        if errors:
            zf.writestr("generation_errors.txt", "\n".join(errors))
    return buffer.getvalue()


def _download_button(**kwargs) -> None:
    try:
        st.download_button(**kwargs, on_click="ignore")
    except TypeError:
        st.download_button(**kwargs)


def _run_lesson(
    bundle,
    *,
    subject: str,
    api_key: str,
    model: str,
    enable_ai_accuracy_qa: bool,
    output_dir: str,
    max_attempts: int = 3,
) -> dict[str, Any]:
    last_exc: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        started = time.monotonic()
        lesson_work_dir = tempfile.mkdtemp(
            prefix=f"lesson_{_safe_filename(bundle.lesson_id)}_",
            dir=output_dir,
        )
        try:
            output_path, report = process_lesson(
                bundle,
                subject=subject,
                api_key=api_key,
                model=model,
                enable_ai_accuracy_qa=enable_ai_accuracy_qa,
                output_dir=lesson_work_dir,
                progress_callback=None,
            )

            if int(report.get("generated_questions", -1)) != TOTAL_GENERATED:
                raise RuntimeError(
                    f"Expected {TOTAL_GENERATED} generated questions, "
                    f"got {report.get('generated_questions')}."
                )

            final_name = Path(output_path).name
            final_path = os.path.join(output_dir, final_name)
            if os.path.abspath(output_path) != os.path.abspath(final_path):
                shutil.move(output_path, final_path)

            return {
                "success": True,
                "bundle": bundle,
                "report": report,
                "output_path": final_path,
                "attempt": attempt,
                "elapsed": int(time.monotonic() - started),
            }

        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                time.sleep(4 * attempt)
        finally:
            shutil.rmtree(lesson_work_dir, ignore_errors=True)

    return {
        "success": False,
        "bundle": bundle,
        "error": (
            f"{type(last_exc).__name__}: {last_exc}"
            if last_exc is not None
            else "Unknown lesson failure"
        ),
        "attempt": max_attempts,
    }


api_key = _get_api_key()

with st.sidebar:
    st.markdown("### JoVE Internal")
    st.markdown("## Quiz Expansion Setup")

    if api_key:
        st.success("OpenAI API key configured.")
    else:
        st.error("OPENAI_API_KEY is not configured in Streamlit Secrets.")

    subject = st.text_input(
        "Subject",
        value="Chemistry",
        help="Context only. PageText + Transcript/CC remain the source of truth.",
    )
    model = st.selectbox(
        "Model",
        ["gpt-5.5", "gpt-4.1", "gpt-4o"],
        index=0,
    )
    enable_ai_accuracy_qa = st.checkbox(
        "Run AI accuracy QA",
        value=True,
    )
    parallel_workers = int(
        st.number_input(
            "Parallel lesson workers",
            min_value=2,
            max_value=12,
            value=12,
            step=1,
            help=(
                "Runs lessons in bounded parallel waves. "
                "If rate-limit failures are detected, the next wave automatically uses fewer workers."
            ),
        )
    )

    st.markdown("---")
    st.markdown(
        f"""
**Locked output rules**
- Existing questions preserved
- **{TOTAL_GENERATED} new questions per lesson**
- {PER_TYPE} each: {', '.join(GENERATED_TYPES)}
- Single Correct included
- Equation + LaTeX dual format retained
- Match/Categorisation unchanged
- One red separator row between old/new
- Yellow/red QA flags retained
"""
    )
    st.caption(f"{APP_BUILD} | Generator {PIPELINE_VERSION}")

st.markdown(
    """
<div class="jove-header">
  <div class="jove-brand">JoVE</div>
  <div class="jove-title">Quiz Library Expansion Generator</div>
  <div class="jove-sub">Hosted Streamlit batch mode using the same save → auto-download → clear-memory pattern as the original JoVE Quiz Generator.</div>
</div>
""",
    unsafe_allow_html=True,
)

if not api_key:
    st.error(
        "Add OPENAI_API_KEY to Streamlit Secrets before starting generation."
    )
    st.stop()

st.info(
    "Each completed lesson Excel is automatically sent to your browser as soon as that lesson finishes. "
    "A manual download button remains visible for the latest completed file, and checkpoint/final ZIP downloads are also retained."
)

uploaded_files = st.file_uploader(
    "Upload chapter ZIPs or lesson source files",
    type=["zip", "docx", "vtt", "txt"],
    accept_multiple_files=True,
    help="Multiple chapter ZIPs can be uploaded together.",
)

if not uploaded_files:
    st.info("Upload one or more source ZIPs to begin.")
    st.stop()

records, upload_errors, temp_dir = collect_uploaded_files(uploaded_files)
bundles = bundle_lessons(records)

for error in upload_errors:
    st.error(error)

preview_rows = []
for bundle in bundles:
    preview_rows.append(
        {
            "Lesson ID": bundle.lesson_id,
            "Chapter": bundle.chapter_key,
            "PageText": bundle.pagetext.name if bundle.pagetext else "NOT DETECTED",
            "Transcript / CC": bundle.transcript.name if bundle.transcript else "NOT DETECTED",
            "Existing Quiz": bundle.quiz.name if bundle.quiz else "NOT DETECTED",
            "Status": "Ready" if bundle.ready else "Needs review",
            "Warnings / Errors": " | ".join(bundle.errors + bundle.warnings),
        }
    )

st.markdown("### Detected lessons")
st.dataframe(
    pd.DataFrame(preview_rows),
    use_container_width=True,
    hide_index=True,
)

ready = [bundle for bundle in bundles if bundle.ready]
issues = [bundle for bundle in bundles if not bundle.ready]

m1, m2, m3 = st.columns(3)
m1.metric(
    "Lessons detected",
    len([bundle for bundle in bundles if bundle.lesson_id != "UNASSIGNED"]),
)
m2.metric("Ready", len(ready))
m3.metric("New questions planned", len(ready) * TOTAL_GENERATED)

if issues:
    st.warning(
        f"{len(issues)} lesson bundle(s) need input review and will be skipped."
    )

upload_signature = tuple(
    (uploaded.name, getattr(uploaded, "size", None))
    for uploaded in uploaded_files
)
if st.session_state.upload_signature != upload_signature:
    st.session_state.upload_signature = upload_signature
    st.session_state.batch_result = None


def _render_final_result(result: dict[str, Any]) -> None:
    st.markdown("---")
    st.markdown("### Batch results")

    summary_df = pd.DataFrame(result.get("summaries", []))
    if not summary_df.empty:
        st.dataframe(summary_df, use_container_width=True, hide_index=True)

    if result.get("errors"):
        with st.expander(
            f"Failed/skipped lessons ({len(result['errors'])})"
        ):
            for message in result["errors"]:
                st.error(message)

    _download_button(
        label="Download all completed lesson Excel files ZIP",
        data=result["zip_bytes"],
        file_name="JoVE_Quiz_Library_Expansion.zip",
        mime="application/zip",
        use_container_width=True,
        type="primary",
        key="final_result_zip",
    )


st.markdown("---")
st.markdown("### Generate batch")
st.write(
    f"Ready lessons: **{len(ready)}** | "
    f"Parallel workers: **{parallel_workers}** | "
    "Each completed Excel will auto-download immediately."
)

if st.button(
    f"Generate {len(ready)} lesson(s)",
    type="primary",
    use_container_width=True,
    disabled=(not ready or not subject.strip()),
):
    progress_bar = st.progress(0)
    status_area = st.empty()
    live_table = st.empty()
    latest_download_area = st.empty()
    checkpoint_download_area = st.empty()
    auto_download_area = st.empty()

    output_dir = tempfile.mkdtemp(prefix="jove_expansion_outputs_")
    output_files: list[tuple[str, str]] = []
    summaries: list[dict[str, Any]] = []
    errors: list[str] = []

    completed_count = 0
    failed_count = 0
    current_workers = max(2, min(parallel_workers, 12))
    remaining = list(ready)
    checkpoint_zip_bytes: bytes | None = None
    checkpoint_number = 0

    try:
        while remaining:
            wave = remaining[:current_workers]
            remaining = remaining[current_workers:]

            status_area.info(
                f"Processing next {len(wave)} lesson(s) with "
                f"{current_workers} parallel workers. "
                f"{completed_count + failed_count}/{len(ready)} finished."
            )

            with ThreadPoolExecutor(
                max_workers=min(current_workers, len(wave)),
                thread_name_prefix="jove-expansion",
            ) as executor:
                future_map = {
                    executor.submit(
                        _run_lesson,
                        bundle,
                        subject=subject,
                        api_key=api_key,
                        model=model,
                        enable_ai_accuracy_qa=enable_ai_accuracy_qa,
                        output_dir=output_dir,
                    ): bundle
                    for bundle in wave
                }

                pending = set(future_map)
                rate_limit_failures = 0

                while pending:
                    done, pending = wait(
                        pending,
                        timeout=1.0,
                        return_when=FIRST_COMPLETED,
                    )

                    for future in done:
                        result = future.result()
                        bundle = result["bundle"]

                        if result["success"]:
                            completed_count += 1
                            output_path = result["output_path"]
                            output_name = Path(output_path).name
                            archive_path = (
                                f"{_safe_filename(bundle.chapter_key)}/"
                                f"{_safe_filename(bundle.lesson_id)}/"
                                f"{output_name}"
                            )
                            output_files.append((archive_path, output_path))

                            with open(output_path, "rb") as handle:
                                excel_bytes = handle.read()

                            report = result["report"]
                            summaries.append(
                                {
                                    "Lesson ID": report.get(
                                        "lesson_id",
                                        bundle.lesson_id,
                                    ),
                                    "Lesson Title": report.get(
                                        "lesson_title",
                                        "",
                                    ),
                                    "Chapter": report.get(
                                        "chapter_name",
                                        bundle.chapter_key,
                                    ),
                                    "Existing Questions": report.get(
                                        "existing_questions",
                                        "",
                                    ),
                                    "New Questions": report.get(
                                        "generated_questions",
                                        "",
                                    ),
                                    "Total Questions": report.get(
                                        "total_questions",
                                        "",
                                    ),
                                    "New Review Flags": report.get(
                                        "generated_review_count",
                                        "",
                                    ),
                                    "New Red Flags": report.get(
                                        "generated_fail_count",
                                        "",
                                    ),
                                    "QA Replacements": report.get(
                                        "qa_replacements",
                                        0,
                                    ),
                                    "QA Repair Rounds": report.get(
                                        "qa_repair_rounds",
                                        0,
                                    ),
                                    "Attempts": result.get("attempt", 1),
                                    "Elapsed Seconds": result.get("elapsed", ""),
                                    "Output File": output_name,
                                    "Status": "Completed",
                                }
                            )

                            with latest_download_area.container():
                                st.success(
                                    f"Completed {completed_count}/{len(ready)}: "
                                    f"Lesson {bundle.lesson_id}"
                                )
                                _download_button(
                                    label=(
                                        "Download latest completed lesson: "
                                        f"{output_name}"
                                    ),
                                    data=excel_bytes,
                                    file_name=output_name,
                                    mime=(
                                        "application/vnd.openxmlformats-"
                                        "officedocument.spreadsheetml.sheet"
                                    ),
                                    use_container_width=True,
                                    key=(
                                        f"latest_{completed_count}_"
                                        f"{_safe_filename(bundle.lesson_id)}"
                                    ),
                                )

                            auto_download_area.empty()
                            with auto_download_area.container():
                                _auto_download_bytes(
                                    excel_bytes,
                                    output_name,
                                    (
                                        "application/vnd.openxmlformats-"
                                        "officedocument.spreadsheetml.sheet"
                                    ),
                                    (
                                        f"lesson-{completed_count}-"
                                        f"{_safe_filename(bundle.lesson_id)}"
                                    ),
                                )

                            # Checkpoint ZIP every 25 completed lessons so a browser
                            # that blocks one of the individual downloads still has
                            # a compact recovery package.
                            if completed_count % 25 == 0:
                                checkpoint_number += 1
                                checkpoint_zip_bytes = _build_zip_bytes(
                                    output_files,
                                    summaries,
                                    errors,
                                )
                                with checkpoint_download_area.container():
                                    _download_button(
                                        label=(
                                            f"Download checkpoint ZIP "
                                            f"({completed_count} completed lessons)"
                                        ),
                                        data=checkpoint_zip_bytes,
                                        file_name=(
                                            "JoVE_Quiz_Library_Expansion_"
                                            f"Checkpoint_{checkpoint_number:02d}.zip"
                                        ),
                                        mime="application/zip",
                                        use_container_width=True,
                                        key=(
                                            f"checkpoint_{checkpoint_number}_"
                                            f"{completed_count}"
                                        ),
                                    )

                            # Release the per-file bytes after the browser component
                            # and manual button have been rendered.
                            del excel_bytes
                            gc.collect()

                        else:
                            failed_count += 1
                            error_text = (
                                f"Lesson {bundle.lesson_id}: "
                                f"{result.get('error', 'Unknown failure')}"
                            )
                            errors.append(error_text)
                            summaries.append(
                                {
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
                                    "Attempts": result.get("attempt", 3),
                                    "Elapsed Seconds": "",
                                    "Output File": "",
                                    "Status": "Failed",
                                }
                            )

                            lower_error = error_text.lower()
                            if (
                                "429" in lower_error
                                or "rate limit" in lower_error
                                or "too many requests" in lower_error
                            ):
                                rate_limit_failures += 1

                        finished = completed_count + failed_count
                        progress_bar.progress(
                            min(
                                100,
                                int(
                                    finished
                                    / max(1, len(ready))
                                    * 100
                                ),
                            )
                        )
                        live_table.dataframe(
                            pd.DataFrame(summaries[-200:]),
                            use_container_width=True,
                            hide_index=True,
                        )
                        status_area.info(
                            f"{finished}/{len(ready)} finished | "
                            f"{completed_count} completed | "
                            f"{failed_count} failed | "
                            f"{len(pending)} active in current wave"
                        )

            # Only affect the NEXT bounded wave; no completed work is repeated.
            if rate_limit_failures:
                previous_workers = current_workers
                current_workers = max(2, current_workers // 2)
                status_area.warning(
                    f"Rate limiting detected. Reducing parallel workers "
                    f"{previous_workers} → {current_workers} for the next wave."
                )
                time.sleep(8)

            gc.collect()

        if not output_files:
            raise RuntimeError(
                "No lesson Excel files were created."
            )

        final_zip_bytes = _build_zip_bytes(
            output_files,
            summaries,
            errors,
        )

        auto_download_area.empty()
        with auto_download_area.container():
            _auto_download_bytes(
                final_zip_bytes,
                "JoVE_Quiz_Library_Expansion.zip",
                "application/zip",
                "final-expansion-batch",
            )

        st.session_state.batch_result = {
            "zip_bytes": final_zip_bytes,
            "summaries": summaries,
            "errors": errors,
        }

        progress_bar.progress(100)
        status_area.success(
            f"Batch complete: {completed_count} completed, "
            f"{failed_count} failed."
        )

    except Exception as exc:
        errors.append(
            f"Batch orchestration error: {type(exc).__name__}: {exc}"
        )

        # Preserve whatever completed output exists even if the outer batch loop
        # itself encounters a problem.
        if output_files:
            partial_zip = _build_zip_bytes(
                output_files,
                summaries,
                errors,
            )
            st.session_state.batch_result = {
                "zip_bytes": partial_zip,
                "summaries": summaries,
                "errors": errors,
            }
            st.error(
                "The batch loop stopped unexpectedly, but all completed lesson "
                "files were preserved in the partial ZIP below."
            )
        else:
            st.session_state.batch_result = None
            st.error(
                f"Batch generation failed before any lesson completed: {exc}"
            )

        with st.expander("Technical error details"):
            st.code(traceback.format_exc())

    finally:
        # File bytes needed after the run are already held in session_state.
        # Remove server-side temporary source/output files to keep memory/disk use bounded.
        shutil.rmtree(temp_dir, ignore_errors=True)
        shutil.rmtree(output_dir, ignore_errors=True)
        gc.collect()

if st.session_state.batch_result is not None:
    _render_final_result(st.session_state.batch_result)
