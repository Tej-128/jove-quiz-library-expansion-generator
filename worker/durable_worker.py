from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import tempfile
import time
import traceback
import zipfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

from durable_queue import (
    DONE_PREFIX,
    ERROR_PREFIX,
    PART_PREFIX,
    QUEUE_PREFIX,
    RESULT_PREFIX,
    STATUS_PREFIX,
    _client,
    _file_created,
    _file_id,
    _file_name,
    _list_all_user_files,
    download_file_bytes,
    upload_bytes,
    upload_path,
)
from input_parser import bundle_lessons, records_from_directory
from pipeline import PIPELINE_VERSION, process_lesson

MAX_RUNTIME_SECONDS = 5 * 60 * 60
DEFAULT_PART_SIZE = 25
DEFAULT_MAX_ATTEMPTS = 3


def _safe_extract(zip_path: str, destination: str) -> None:
    root = Path(destination).resolve()
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.infolist():
            target = (root / member.filename).resolve()
            if root != target and root not in target.parents:
                raise RuntimeError(f"Unsafe ZIP member path: {member.filename}")
        zf.extractall(destination)


def _json_file(client, file_obj) -> dict:
    return json.loads(download_file_bytes(client, _file_id(file_obj)).decode("utf-8"))


def _upload_json(client, filename: str, payload: dict):
    return upload_bytes(
        client,
        json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        filename,
        purpose="user_data",
    )


def _parse_result_name(filename: str, job_id: str):
    match = re.match(
        rf"^{re.escape(RESULT_PREFIX + job_id + '_')}(\d+)__(.+\.xlsx)$",
        filename,
        re.I,
    )
    if not match:
        return None
    return match.group(1), match.group(2)


def _parse_error_name(filename: str, job_id: str):
    match = re.match(
        rf"^{re.escape(ERROR_PREFIX + job_id + '_')}(\d+)_attempt(\d+)\.json$",
        filename,
        re.I,
    )
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _discover_pending_job(client):
    files = _list_all_user_files(client)
    queue_files = sorted(
        [f for f in files if _file_name(f).startswith(QUEUE_PREFIX) and _file_name(f).endswith(".json")],
        key=_file_created,
    )
    names = {_file_name(f) for f in files}
    for queue_file in queue_files:
        job_id = _file_name(queue_file)[len(QUEUE_PREFIX):-5]
        if f"{DONE_PREFIX}{job_id}.json" in names:
            continue
        return queue_file, files
    return None, files


def _output_filename(output_path: str) -> str:
    return os.path.basename(output_path)


def _build_part_zip(
    *,
    client,
    job_id: str,
    part_number: int,
    lesson_ids: list[str],
    result_files: dict[str, object],
    permanent_failures: dict[str, str],
    bundle_by_id: dict[str, object],
) -> bytes:
    buf = io.BytesIO()
    summary = io.StringIO()
    writer = csv.DictWriter(summary, fieldnames=["Lesson ID", "Status", "Detail"])
    writer.writeheader()

    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for lesson_id in lesson_ids:
            file_obj = result_files.get(lesson_id)
            if file_obj:
                parsed = _parse_result_name(_file_name(file_obj), job_id)
                original_name = parsed[1] if parsed else f"{lesson_id}_Quiz_Expanded.xlsx"
                bundle = bundle_by_id.get(lesson_id)
                chapter = getattr(bundle, "chapter_key", "Uploaded_Chapter") or "Uploaded_Chapter"
                arcname = f"{chapter}/{lesson_id}/{original_name}"
                zf.writestr(arcname, download_file_bytes(client, _file_id(file_obj)))
                writer.writerow({"Lesson ID": lesson_id, "Status": "Completed", "Detail": ""})
            else:
                detail = permanent_failures.get(lesson_id, "No completed workbook.")
                writer.writerow({"Lesson ID": lesson_id, "Status": "Failed", "Detail": detail})
        zf.writestr("generation_summary.csv", summary.getvalue())
    return buf.getvalue()


def _run_lesson(bundle, queue_info: dict, output_dir: str):
    started = time.monotonic()
    output_path, report = process_lesson(
        bundle,
        subject=queue_info["subject"],
        api_key=os.environ["OPENAI_API_KEY"],
        model=queue_info["model"],
        enable_ai_accuracy_qa=bool(queue_info.get("enable_ai_accuracy_qa", True)),
        output_dir=output_dir,
        progress_callback=None,
    )
    return output_path, report, int(time.monotonic() - started)


def main() -> int:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY GitHub Actions secret is missing.", flush=True)
        return 2

    client = _client(api_key)
    queue_file, initial_files = _discover_pending_job(client)
    if not queue_file:
        print("No pending JoVE durable quiz jobs.", flush=True)
        return 0

    queue_info = _json_file(client, queue_file)
    job_id = str(queue_info["job_id"])
    print(f"Picked durable job {job_id}.", flush=True)

    if queue_info.get("build_version") != PIPELINE_VERSION:
        message = (
            f"Build mismatch: queued {queue_info.get('build_version')} but worker is {PIPELINE_VERSION}. "
            "Resubmit the job from the current app build."
        )
        print(message, flush=True)
        _upload_json(
            client,
            f"{DONE_PREFIX}{job_id}.json",
            {"job_id": job_id, "completed": 0, "failed": len(queue_info.get("ready_lesson_ids", [])), "error": message},
        )
        return 3

    work_root = tempfile.mkdtemp(prefix=f"jove_durable_{job_id}_")
    try:
        source_zip = os.path.join(work_root, "source.zip")
        source_bytes = download_file_bytes(client, queue_info["source_file_id"])
        Path(source_zip).write_bytes(source_bytes)
        extracted = os.path.join(work_root, "source")
        os.makedirs(extracted, exist_ok=True)
        _safe_extract(source_zip, extracted)

        records = records_from_directory(extracted)
        bundles = bundle_lessons(records)
        bundle_by_id = {b.lesson_id: b for b in bundles if b.lesson_id != "UNASSIGNED"}

        expected_ids = [str(value) for value in queue_info.get("ready_lesson_ids", [])]
        missing_inputs = [lesson_id for lesson_id in expected_ids if lesson_id not in bundle_by_id]
        if missing_inputs:
            print(f"Warning: {len(missing_inputs)} queued lesson IDs are not present after extraction.", flush=True)

        all_files = _list_all_user_files(client)
        job_files = [f for f in all_files if job_id in _file_name(f)]

        result_files: dict[str, object] = {}
        error_attempts: dict[str, int] = {}
        error_messages: dict[str, str] = {}
        part_names = set()

        for file_obj in job_files:
            name = _file_name(file_obj)
            parsed_result = _parse_result_name(name, job_id)
            if parsed_result:
                result_files[parsed_result[0]] = file_obj
                continue
            parsed_error = _parse_error_name(name, job_id)
            if parsed_error:
                lesson_id, attempt = parsed_error
                error_attempts[lesson_id] = max(error_attempts.get(lesson_id, 0), attempt)
                try:
                    payload = _json_file(client, file_obj)
                    error_messages[lesson_id] = str(payload.get("error", ""))
                except Exception:
                    pass
                continue
            if name.startswith(f"{PART_PREFIX}{job_id}_"):
                part_names.add(name)

        part_size = max(1, int(queue_info.get("part_size", DEFAULT_PART_SIZE) or DEFAULT_PART_SIZE))
        max_attempts = max(1, int(queue_info.get("max_lesson_attempts", DEFAULT_MAX_ATTEMPTS) or DEFAULT_MAX_ATTEMPTS))
        worker_count = max(1, min(int(queue_info.get("workers", 4) or 4), 6))

        started = time.monotonic()
        total = len(expected_ids)

        for part_index, start in enumerate(range(0, total, part_size), 1):
            if time.monotonic() - started > MAX_RUNTIME_SECONDS:
                print("Reached safe workflow runtime budget; next scheduled run will resume.", flush=True)
                return 0

            part_ids = expected_ids[start:start + part_size]
            part_filename = f"{PART_PREFIX}{job_id}_{part_index:03d}.zip"
            if part_filename in part_names:
                print(f"Part {part_index:03d} already complete; skipping.", flush=True)
                continue

            pending = [
                lesson_id
                for lesson_id in part_ids
                if lesson_id not in result_files
                and lesson_id in bundle_by_id
                and error_attempts.get(lesson_id, 0) < max_attempts
            ]

            while pending:
                if time.monotonic() - started > MAX_RUNTIME_SECONDS:
                    print("Reached safe workflow runtime budget during part; next run will resume.", flush=True)
                    return 0

                output_dir = os.path.join(work_root, f"part_{part_index:03d}_outputs")
                os.makedirs(output_dir, exist_ok=True)
                round_ids = list(pending)
                pending = []

                with ThreadPoolExecutor(max_workers=min(worker_count, len(round_ids))) as executor:
                    future_map = {
                        executor.submit(_run_lesson, bundle_by_id[lesson_id], queue_info, output_dir): lesson_id
                        for lesson_id in round_ids
                    }
                    unfinished = set(future_map)
                    while unfinished:
                        done, unfinished = wait(unfinished, return_when=FIRST_COMPLETED)
                        for future in done:
                            lesson_id = future_map[future]
                            attempt = error_attempts.get(lesson_id, 0) + 1
                            try:
                                output_path, report, elapsed = future.result()
                                original_name = _output_filename(output_path)
                                upload_name = f"{RESULT_PREFIX}{job_id}_{lesson_id}__{original_name}"
                                uploaded = upload_path(
                                    client,
                                    output_path,
                                    filename=upload_name,
                                    purpose="user_data",
                                )
                                result_files[lesson_id] = uploaded
                                print(
                                    f"Lesson {lesson_id} completed and checkpointed ({elapsed}s).",
                                    flush=True,
                                )
                            except Exception as exc:
                                error_attempts[lesson_id] = attempt
                                error_messages[lesson_id] = f"{type(exc).__name__}: {exc}"
                                _upload_json(
                                    client,
                                    f"{ERROR_PREFIX}{job_id}_{lesson_id}_attempt{attempt}.json",
                                    {
                                        "job_id": job_id,
                                        "lesson_id": lesson_id,
                                        "attempt": attempt,
                                        "error": error_messages[lesson_id],
                                        "traceback": traceback.format_exc(limit=8),
                                        "created_at": int(time.time()),
                                    },
                                )
                                print(
                                    f"Lesson {lesson_id} failed attempt {attempt}/{max_attempts}: {exc}",
                                    flush=True,
                                )
                                if attempt < max_attempts:
                                    pending.append(lesson_id)

                if pending:
                    time.sleep(5)

            permanent_failures = {
                lesson_id: (
                    "Input bundle missing after source extraction."
                    if lesson_id not in bundle_by_id
                    else error_messages.get(lesson_id, "Failed after maximum retry attempts.")
                )
                for lesson_id in part_ids
                if lesson_id not in result_files
            }

            part_payload = _build_part_zip(
                client=client,
                job_id=job_id,
                part_number=part_index,
                lesson_ids=part_ids,
                result_files=result_files,
                permanent_failures=permanent_failures,
                bundle_by_id=bundle_by_id,
            )
            uploaded_part = upload_bytes(
                client,
                part_payload,
                part_filename,
                purpose="user_data",
            )
            part_names.add(part_filename)

            completed_count = sum(1 for lesson_id in expected_ids if lesson_id in result_files)
            permanent_count = sum(
                1
                for lesson_id in expected_ids
                if lesson_id not in result_files
                and (
                    lesson_id not in bundle_by_id
                    or error_attempts.get(lesson_id, 0) >= max_attempts
                )
            )
            _upload_json(
                client,
                f"{STATUS_PREFIX}{job_id}_{part_index:03d}.json",
                {
                    "job_id": job_id,
                    "part": part_index,
                    "part_file_id": uploaded_part.id,
                    "completed": completed_count,
                    "failed": permanent_count,
                    "total": total,
                    "updated_at": int(time.time()),
                },
            )
            print(
                f"Part {part_index:03d} uploaded. Overall completed={completed_count}, failed={permanent_count}, total={total}.",
                flush=True,
            )

        completed_count = sum(1 for lesson_id in expected_ids if lesson_id in result_files)
        failed_ids = [
            lesson_id
            for lesson_id in expected_ids
            if lesson_id not in result_files
        ]
        _upload_json(
            client,
            f"{DONE_PREFIX}{job_id}.json",
            {
                "job_id": job_id,
                "completed": completed_count,
                "failed": len(failed_ids),
                "total": total,
                "failed_lesson_ids": failed_ids,
                "build_version": PIPELINE_VERSION,
                "finished_at": int(time.time()),
            },
        )
        print(
            f"Durable job {job_id} finished: completed={completed_count}, failed={len(failed_ids)}, total={total}.",
            flush=True,
        )
        return 0
    finally:
        shutil.rmtree(work_root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
