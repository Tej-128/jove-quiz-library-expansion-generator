from __future__ import annotations

import io
import json
import os
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from openai import OpenAI

FILE_EXPIRY_SECONDS = 7 * 24 * 60 * 60
QUEUE_PREFIX = "jove_queue_"
SOURCE_PREFIX = "jove_source_"
RESULT_PREFIX = "jove_result_"
PART_PREFIX = "jove_part_"
STATUS_PREFIX = "jove_status_"
DONE_PREFIX = "jove_done_"
ERROR_PREFIX = "jove_error_"


def _client(api_key: str) -> OpenAI:
    return OpenAI(api_key=api_key, timeout=120.0, max_retries=3)


def _upload_with_expiry(client: OpenAI, file_obj, *, purpose: str = "user_data"):
    try:
        return client.files.create(
            file=file_obj,
            purpose=purpose,
            expires_after={"anchor": "created_at", "seconds": FILE_EXPIRY_SECONDS},
        )
    except Exception as exc:
        message = str(exc).lower()
        if "expires_after" not in message and "unexpected keyword" not in message:
            raise
        try:
            file_obj.seek(0)
        except Exception:
            pass
        return client.files.create(file=file_obj, purpose=purpose)


def upload_bytes(
    client: OpenAI,
    payload: bytes,
    filename: str,
    *,
    purpose: str = "user_data",
):
    buffer = io.BytesIO(payload)
    buffer.name = filename
    return _upload_with_expiry(client, buffer, purpose=purpose)


def upload_path(
    client: OpenAI,
    path: str,
    *,
    filename: str | None = None,
    purpose: str = "user_data",
):
    if filename and filename != os.path.basename(path):
        with open(path, "rb") as source:
            payload = source.read()
        return upload_bytes(client, payload, filename, purpose=purpose)
    with open(path, "rb") as source:
        return _upload_with_expiry(client, source, purpose=purpose)


def download_file_bytes(client: OpenAI, file_id: str) -> bytes:
    response = client.files.content(file_id)
    if hasattr(response, "read"):
        data = response.read()
        if isinstance(data, str):
            return data.encode("utf-8")
        return bytes(data)
    if hasattr(response, "content"):
        return bytes(response.content)
    if hasattr(response, "text"):
        return str(response.text).encode("utf-8")
    raise RuntimeError(f"Could not read OpenAI file {file_id}.")


def _list_all_user_files(client: OpenAI) -> list[Any]:
    try:
        page = client.files.list(purpose="user_data")
    except TypeError:
        page = client.files.list()

    items: list[Any] = []
    while True:
        items.extend(list(getattr(page, "data", []) or []))
        has_next = getattr(page, "has_next_page", None)
        get_next = getattr(page, "get_next_page", None)
        if not callable(has_next) or not has_next() or not callable(get_next):
            break
        page = get_next()
    return items


def _safe_arcname(value: str, fallback: str) -> str:
    text = str(value or "").replace("\\", "/").lstrip("/")
    parts = [part for part in text.split("/") if part not in {"", ".", ".."}]
    return "/".join(parts) or fallback


def _build_source_zip(records) -> str:
    fd, path = tempfile.mkstemp(prefix="jove_durable_source_", suffix=".zip")
    os.close(fd)
    used: set[str] = set()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, record in enumerate(records, 1):
            arcname = _safe_arcname(
                getattr(record, "relative_path", "") or getattr(record, "name", ""),
                f"file_{idx:06d}_{getattr(record, 'name', 'source')}",
            )
            if arcname in used:
                arcname = f"duplicate_{idx:06d}/{arcname}"
            used.add(arcname)
            zf.write(record.path, arcname)
    return path


def submit_durable_job(
    *,
    records,
    ready_lesson_ids: list[str],
    subject: str,
    model: str,
    enable_ai_accuracy_qa: bool,
    workers: int,
    api_key: str,
    build_version: str,
) -> dict[str, str]:
    client = _client(api_key)
    job_id = uuid.uuid4().hex[:16]
    source_path = _build_source_zip(records)
    try:
        source_file = upload_path(
            client,
            source_path,
            filename=f"{SOURCE_PREFIX}{job_id}.zip",
            purpose="user_data",
        )
    finally:
        try:
            os.remove(source_path)
        except OSError:
            pass

    queue_payload = {
        "job_id": job_id,
        "build_version": build_version,
        "subject": subject.strip(),
        "model": model,
        "enable_ai_accuracy_qa": bool(enable_ai_accuracy_qa),
        "workers": max(1, min(int(workers), 6)),
        "ready_lesson_ids": [str(value) for value in ready_lesson_ids],
        "source_file_id": source_file.id,
        "source_filename": source_file.filename,
        "submitted_at": int(time.time()),
        "part_size": 25,
        "max_lesson_attempts": 3,
    }
    queue_file = upload_bytes(
        client,
        json.dumps(queue_payload, ensure_ascii=False, indent=2).encode("utf-8"),
        f"{QUEUE_PREFIX}{job_id}.json",
        purpose="user_data",
    )
    return {
        "job_id": job_id,
        "queue_file_id": queue_file.id,
        "source_file_id": source_file.id,
    }


def _file_name(file_obj: Any) -> str:
    return str(getattr(file_obj, "filename", "") or "")


def _file_created(file_obj: Any) -> int:
    try:
        return int(getattr(file_obj, "created_at", 0) or 0)
    except Exception:
        return 0


def _file_id(file_obj: Any) -> str:
    return str(getattr(file_obj, "id", "") or "")


def _result_lesson_id(filename: str, job_id: str) -> str:
    prefix = f"{RESULT_PREFIX}{job_id}_"
    if not filename.startswith(prefix) or "__" not in filename:
        return ""
    return filename[len(prefix):].split("__", 1)[0].strip()


def _latest_json(client: OpenAI, files: list[Any]) -> dict[str, Any] | None:
    if not files:
        return None
    latest = max(files, key=_file_created)
    try:
        return json.loads(download_file_bytes(client, _file_id(latest)).decode("utf-8"))
    except Exception:
        return None


def get_durable_job_status(api_key: str, job_id: str) -> dict[str, Any]:
    client = _client(api_key)
    all_files = _list_all_user_files(client)
    matching = [f for f in all_files if job_id in _file_name(f)]

    queue_files = [f for f in matching if _file_name(f) == f"{QUEUE_PREFIX}{job_id}.json"]
    if not queue_files:
        return {
            "job_id": job_id,
            "state": "not_found",
            "message": "The durable job record was not found. It may have expired or the job ID is incorrect.",
            "parts": [],
        }

    queue_info = _latest_json(client, queue_files) or {}
    expected = len(queue_info.get("ready_lesson_ids", []) or [])

    part_files = sorted(
        [f for f in matching if _file_name(f).startswith(f"{PART_PREFIX}{job_id}_")],
        key=_file_name,
    )
    status_files = [f for f in matching if _file_name(f).startswith(f"{STATUS_PREFIX}{job_id}_")]
    done_files = [f for f in matching if _file_name(f) == f"{DONE_PREFIX}{job_id}.json"]
    error_files = [f for f in matching if _file_name(f).startswith(f"{ERROR_PREFIX}{job_id}_")]
    result_files = [f for f in matching if _file_name(f).startswith(f"{RESULT_PREFIX}{job_id}_")]

    latest_status = _latest_json(client, status_files) or {}
    done_payload = _latest_json(client, done_files) or {}

    completed_ids = sorted({
        lesson_id
        for lesson_id in (_result_lesson_id(_file_name(f), job_id) for f in result_files)
        if lesson_id
    })
    completed = len(completed_ids)
    if done_files:
        completed = int(done_payload.get("completed", completed) or completed)
    failed = int(done_payload.get("failed", latest_status.get("failed", 0)) or 0)

    recent_completed = [
        _result_lesson_id(_file_name(f), job_id)
        for f in sorted(result_files, key=_file_created, reverse=True)[:8]
    ]
    recent_completed = [lesson_id for lesson_id in recent_completed if lesson_id]

    if done_files:
        state = "completed" if failed == 0 else "completed_with_failures"
    elif status_files or part_files or result_files:
        state = "in_progress"
    else:
        state = "queued"

    return {
        "job_id": job_id,
        "state": state,
        "subject": queue_info.get("subject", ""),
        "model": queue_info.get("model", ""),
        "build_version": queue_info.get("build_version", ""),
        "expected": expected,
        "completed": completed,
        "failed": failed,
        "recent_completed": recent_completed,
        "part_size": int(queue_info.get("part_size", 25) or 25),
        "submitted_at": queue_info.get("submitted_at"),
        "parts": [
            {
                "name": _file_name(f),
                "file_id": _file_id(f),
                "created_at": _file_created(f),
            }
            for f in part_files
        ],
        "error_event_count": len(error_files),
        "done": bool(done_files),
    }


def get_file_bytes_by_id(api_key: str, file_id: str) -> bytes:
    return download_file_bytes(_client(api_key), file_id)
