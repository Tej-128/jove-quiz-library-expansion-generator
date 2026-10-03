from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

JOB_ROOT_NAME = ".jove_jobs"
STATUS_FILENAME = "progress.json"
CONFIG_FILENAME = "job_config.json"
STOP_FILENAME = "STOP_REQUESTED"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: str | Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return dict(default or {})
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return dict(default or {})


def read_job_status(job_dir: str | Path) -> dict[str, Any]:
    job_path = Path(job_dir)
    status = read_json(job_path / STATUS_FILENAME, {})
    config = read_json(job_path / CONFIG_FILENAME, {})
    if not status:
        status = {
            "job_id": config.get("job_id", job_path.name),
            "state": "created",
            "total": len(config.get("lesson_ids", []) or []),
            "completed": 0,
            "failed": 0,
            "in_progress": 0,
            "recent_completed": [],
            "recent_failed": [],
        }
    status["job_dir"] = str(job_path)
    status["output_dir"] = config.get("output_dir", "")
    status["subject"] = config.get("subject", "")
    status["model"] = config.get("model", "")
    status["workers_requested"] = config.get("workers", "")
    status["build_version"] = config.get("build_version", "")
    return status


def list_local_jobs(output_dir: str | Path) -> list[dict[str, Any]]:
    root = Path(output_dir).expanduser()
    jobs_root = root / JOB_ROOT_NAME
    if not jobs_root.is_dir():
        return []
    jobs: list[dict[str, Any]] = []
    for job_dir in jobs_root.iterdir():
        if not job_dir.is_dir() or not (job_dir / CONFIG_FILENAME).exists():
            continue
        status = read_job_status(job_dir)
        status["_mtime"] = (job_dir / CONFIG_FILENAME).stat().st_mtime
        jobs.append(status)
    jobs.sort(key=lambda item: item.get("_mtime", 0), reverse=True)
    return jobs


def _safe_relative_path(value: str, fallback: str) -> Path:
    raw = str(value or "").replace("\\", "/")
    parts = [
        part
        for part in PurePosixPath(raw).parts
        if part not in {"", ".", ".."} and ":" not in part
    ]
    return Path(*parts) if parts else Path(fallback)


def create_local_job(
    *,
    records,
    ready_lesson_ids: list[str],
    output_dir: str,
    subject: str,
    model: str,
    enable_ai_accuracy_qa: bool,
    workers: int,
    build_version: str,
) -> str:
    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    jobs_root = output_root / JOB_ROOT_NAME
    jobs_root.mkdir(parents=True, exist_ok=True)

    job_id = time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:8]
    job_dir = jobs_root / job_id
    input_dir = job_dir / "input"
    work_dir = job_dir / "work"
    input_dir.mkdir(parents=True, exist_ok=False)
    work_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    used: set[str] = set()
    for idx, record in enumerate(records, 1):
        relative = _safe_relative_path(
            getattr(record, "relative_path", "") or getattr(record, "name", ""),
            f"file_{idx:06d}_{getattr(record, 'name', 'source')}",
        )
        key = relative.as_posix().lower()
        if key in used:
            relative = Path(f"duplicate_{idx:06d}") / relative
        used.add(relative.as_posix().lower())
        target = input_dir / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(record.path, target)
        copied += 1

    config = {
        "job_id": job_id,
        "build_version": build_version,
        "created_at": int(time.time()),
        "output_dir": str(output_root),
        "input_dir": str(input_dir),
        "work_dir": str(work_dir),
        "subject": subject.strip(),
        "model": model,
        "enable_ai_accuracy_qa": bool(enable_ai_accuracy_qa),
        "workers": max(1, min(int(workers), 16)),
        "max_lesson_attempts": 3,
        "lesson_ids": [str(value) for value in ready_lesson_ids],
        "copied_source_files": copied,
    }
    _atomic_json(job_dir / CONFIG_FILENAME, config)
    _atomic_json(
        job_dir / STATUS_FILENAME,
        {
            "job_id": job_id,
            "state": "created",
            "total": len(config["lesson_ids"]),
            "completed": 0,
            "failed": 0,
            "in_progress": 0,
            "workers_active": 0,
            "workers_requested": config["workers"],
            "recent_completed": [],
            "recent_failed": [],
            "started_at": None,
            "updated_at": int(time.time()),
            "message": "Job created and ready to start.",
        },
    )
    return str(job_dir)


def request_stop(job_dir: str | Path) -> None:
    Path(job_dir, STOP_FILENAME).write_text(
        f"Stop requested at {time.strftime('%Y-%m-%d %H:%M:%S')}\n",
        encoding="utf-8",
    )


def clear_stop_request(job_dir: str | Path) -> None:
    try:
        Path(job_dir, STOP_FILENAME).unlink()
    except FileNotFoundError:
        pass


def launch_local_supervisor(job_dir: str, api_key: str) -> int:
    if not api_key.strip():
        raise ValueError("OpenAI API key is required before starting the local batch.")

    clear_stop_request(job_dir)
    repo_dir = Path(__file__).resolve().parent
    supervisor = repo_dir / "local_batch_supervisor.py"
    if not supervisor.exists():
        raise FileNotFoundError(f"Missing supervisor script: {supervisor}")

    env = os.environ.copy()
    env["OPENAI_API_KEY"] = api_key.strip()
    env["PYTHONUNBUFFERED"] = "1"
    env["JOVE_LOCAL_MODE"] = "1"

    cmd = [sys.executable, str(supervisor), "--job-dir", str(Path(job_dir).resolve())]
    kwargs: dict[str, Any] = {
        "cwd": str(repo_dir),
        "env": env,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "DETACHED_PROCESS", 0)
        )
    else:
        kwargs["start_new_session"] = True

    process = subprocess.Popen(cmd, **kwargs)
    return int(process.pid)
