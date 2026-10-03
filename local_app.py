"""JoVE Quiz Library Expansion Generator - local production UI."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import pandas as pd
import streamlit as st

from input_parser import bundle_lessons, collect_uploaded_files
from local_batch_runtime import (
    create_local_job,
    launch_local_supervisor,
    list_local_jobs,
    read_job_status,
)
from pipeline import PIPELINE_VERSION
from quiz_generator import GENERATED_TYPES, PER_TYPE, TOTAL_GENERATED

EXPECTED_BUILD = "v1.7.1_windows_safe_inputs"

st.set_page_config(
    page_title="JoVE Quiz Library Expansion Generator",
    page_icon="JQ",
    layout="wide",
    initial_sidebar_state="expanded",
)

if PIPELINE_VERSION != EXPECTED_BUILD or TOTAL_GENERATED != 21:
    st.error(
        f"Build mismatch. Expected {EXPECTED_BUILD} with 21 new questions, "
        f"but loaded {PIPELINE_VERSION} with {TOTAL_GENERATED}."
    )
    st.stop()

st.markdown(
    """
<style>
:root{--jove-red:#d71920;--ink:#20242a;--muted:#68717a;--border:#e3e6ea}
[data-testid="stSidebar"]{background:#f7f7f8;border-right:1px solid var(--border)}
[data-testid="stMetric"]{background:#fff;border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.stButton>button{border-radius:7px;font-weight:700}
.stButton>button[kind="primary"]{background:var(--jove-red);border-color:var(--jove-red);color:#fff}
.jove-header{padding:18px 22px;border:1px solid var(--border);border-left:6px solid var(--jove-red);border-radius:10px;margin-bottom:20px}
.jove-brand{font-size:2rem;font-weight:900;color:var(--jove-red)}
.jove-title{font-size:1.15rem;font-weight:800;color:var(--ink)}
.jove-sub{font-size:.9rem;color:var(--muted);margin-top:5px}
</style>
""",
    unsafe_allow_html=True,
)

st.session_state.setdefault("active_job_dir", "")
st.session_state.setdefault("upload_temp_dir", "")
st.session_state.setdefault("runtime_api_key", "")


def configured_api_key() -> str:
    try:
        secret = st.secrets.get("OPENAI_API_KEY", "")
    except Exception:
        secret = ""
    return str(secret or os.environ.get("OPENAI_API_KEY", "")).strip()


def default_output_folder() -> str:
    desktop = Path.home() / "Desktop"
    return str((desktop if desktop.exists() else Path.home()) / "JoVE_Quiz_Output")


@st.fragment(run_every="2s")
def render_job(job_dir: str) -> None:
    status = read_job_status(job_dir)
    total = int(status.get("total", 0) or 0)
    completed = int(status.get("completed", 0) or 0)
    failed = int(status.get("failed", 0) or 0)
    in_progress = int(status.get("in_progress", 0) or 0)
    state = str(status.get("state", "unknown"))

    st.markdown("## Batch progress")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total lessons", total)
    c2.metric("Completed", completed)
    c3.metric("In progress", in_progress)
    c4.metric("Failed", failed)

    progress = 0.0 if total <= 0 else min(1.0, (completed + failed) / total)
    st.progress(progress, text=f"{completed} completed / {failed} failed / {total} total")

    if status.get("recent_completed"):
        st.caption("Recently completed: " + ", ".join(status["recent_completed"]))
    if status.get("recent_failed"):
        st.caption("Recently retried: " + ", ".join(status["recent_failed"]))

    message = status.get("message", "")
    if state == "running":
        st.success(message)
    elif state == "completed":
        st.success(message or "Batch completed.")
    elif state == "completed_with_failures":
        st.warning(message)
    elif state == "worker_crashed":
        st.warning(
            "Worker crashed, but the detached supervisor is restarting it. "
            "Completed files remain preserved."
        )
    else:
        st.info(message or f"State: {state}")

    output_dir = status.get("output_dir", "")
    if output_dir:
        st.write("Output folder: " + str(output_dir))

    cols = st.columns(2)
    with cols[0]:
        if output_dir and st.button(
            "Open output folder",
            use_container_width=True,
            key="open_output",
        ):
            os.startfile(output_dir)
    with cols[1]:
        if state in {"worker_crashed", "completed_with_failures"}:
            if st.button(
                "Retry unfinished lessons",
                type="primary",
                use_container_width=True,
                key="retry_job",
            ):
                key = st.session_state.get("runtime_api_key", "")
                if not key:
                    st.error("Enter the OpenAI API key in the sidebar.")
                else:
                    pid = launch_local_supervisor(job_dir, key)
                    st.success(f"Retry supervisor started. PID {pid}.")


with st.sidebar:
    st.markdown("### JoVE Internal")
    st.markdown("## Quiz Expansion Setup")

    saved_key = configured_api_key()
    if saved_key:
        runtime_api_key = saved_key
        st.success("OpenAI API key configured.")
    else:
        runtime_api_key = st.text_input(
            "OpenAI API key",
            type="password",
            help="Used only by the local worker and never written into job files.",
        )
    st.session_state["runtime_api_key"] = runtime_api_key

    subject = st.text_input("Subject", value="Chemistry")
    model = st.selectbox("Model", ["gpt-5.5", "gpt-4.1", "gpt-4o"], index=0)
    run_qa = st.checkbox("Run AI accuracy QA", value=True)

    workers = int(
        st.number_input(
            "Parallel lesson workers",
            min_value=4,
            max_value=16,
            value=12,
            step=1,
            help="Start at 12. The worker reduces concurrency automatically if rate limiting occurs.",
        )
    )

    output_folder = st.text_input(
        "Output folder",
        value=default_output_folder(),
        help="Each validated lesson Excel file appears here immediately after completion.",
    )

    st.markdown("---")
    st.markdown(
        f"""
**Locked rules**
- Existing questions preserved
- {TOTAL_GENERATED} new questions per lesson
- {PER_TYPE} each across 7 types
- Single Correct included
- Equation + LaTeX dual format retained
- Match/Categorisation unchanged
- One red separator row between old/new
- QA review flags retained
"""
    )
    st.caption(PIPELINE_VERSION)

st.markdown(
    """
<div class="jove-header">
  <div class="jove-brand">JoVE</div>
  <div class="jove-title">Quiz Library Expansion Generator</div>
  <div class="jove-sub">Local production mode: completed lesson files are saved directly to your chosen Windows folder and unfinished lessons can resume without redoing completed work.</div>
</div>
""",
    unsafe_allow_html=True,
)

active_job = st.session_state.get("active_job_dir", "")
if active_job and Path(active_job).is_dir():
    render_job(active_job)
    if st.button("Return to setup", use_container_width=True):
        st.session_state["active_job_dir"] = ""
        st.rerun()
    st.stop()

try:
    output_root = Path(output_folder).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    output_ok = output_root.is_dir()
except Exception as exc:
    output_root = Path.home()
    output_ok = False
    st.error(f"Output folder error: {exc}")

if output_ok:
    resumable = [
        job
        for job in list_local_jobs(output_root)
        if job.get("state") in {
            "created",
            "running",
            "worker_crashed",
            "completed_with_failures",
        }
    ][:5]
    if resumable:
        with st.expander("Existing resumable jobs"):
            for idx, job in enumerate(resumable):
                cols = st.columns([3, 2, 1])
                cols[0].write(job.get("job_id"))
                cols[1].write(
                    f"{job.get('completed', 0)}/{job.get('total', 0)} "
                    f"- {job.get('state', '')}"
                )
                if cols[2].button("Open", key=f"job_{idx}"):
                    st.session_state["active_job_dir"] = job["job_dir"]
                    st.rerun()

uploaded_files = st.file_uploader(
    "Upload chapter ZIPs or lesson source files",
    type=["zip", "docx", "vtt", "txt"],
    accept_multiple_files=True,
    help="Multiple chapter ZIPs can be uploaded together.",
)

if not uploaded_files:
    st.info("Upload one or more chapter ZIPs to begin.")
    st.stop()

old_temp = st.session_state.get("upload_temp_dir", "")

# Build the new extraction completely BEFORE deleting the previous rerun's temp
# folder. This avoids invalidating FileRecord paths during Streamlit button reruns.
records, upload_errors, temp_dir = collect_uploaded_files(uploaded_files)

if old_temp and old_temp != temp_dir and os.path.isdir(old_temp):
    shutil.rmtree(old_temp, ignore_errors=True)

st.session_state["upload_temp_dir"] = temp_dir
bundles = bundle_lessons(records)

for error in upload_errors:
    st.error(error)

preview = []
for bundle in bundles:
    preview.append(
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
st.dataframe(pd.DataFrame(preview), use_container_width=True, hide_index=True)

ready = [bundle for bundle in bundles if bundle.ready]
issues = [bundle for bundle in bundles if not bundle.ready]

m1, m2, m3 = st.columns(3)
m1.metric("Lessons detected", len([b for b in bundles if b.lesson_id != "UNASSIGNED"]))
m2.metric("Ready", len(ready))
m3.metric("New questions planned", len(ready) * TOTAL_GENERATED)

if issues:
    st.warning(f"{len(issues)} lesson bundle(s) need input review and will be skipped.")
if not runtime_api_key:
    st.warning("Enter the OpenAI API key in the sidebar.")
if not subject.strip():
    st.warning("Enter the subject in the sidebar.")

st.markdown("---")
st.markdown("### Start local resumable batch")
st.write(
    f"{len(ready)} ready lessons | {workers} parallel workers | "
    "each validated Excel file is saved immediately on lesson completion."
)

if st.button(
    f"Start {len(ready)} lesson(s)",
    type="primary",
    use_container_width=True,
    disabled=(
        not ready
        or not runtime_api_key
        or not subject.strip()
        or not output_ok
    ),
):
    with st.spinner("Persisting input files and starting the detached local worker..."):
        job_dir = create_local_job(
            records=records,
            ready_lesson_ids=[b.lesson_id for b in ready],
            output_dir=str(output_root),
            subject=subject,
            model=model,
            enable_ai_accuracy_qa=run_qa,
            workers=workers,
            build_version=PIPELINE_VERSION,
        )
        pid = launch_local_supervisor(job_dir, runtime_api_key)
        shutil.rmtree(temp_dir, ignore_errors=True)
        st.session_state["upload_temp_dir"] = ""
        st.session_state["active_job_dir"] = job_dir

    st.success(f"Batch started. Supervisor PID {pid}.")
    st.rerun()
