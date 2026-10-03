from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from local_batch_runtime import STATUS_FILENAME, STOP_FILENAME, read_json

TERMINAL_STATES = {"completed", "completed_with_failures", "paused"}
MAX_RESTARTS = 20


def append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")


def main(job_dir: str) -> int:
    job_path = Path(job_dir).resolve()
    repo_dir = Path(__file__).resolve().parent
    worker_script = repo_dir / "local_batch_worker.py"
    log_path = job_path / "supervisor.log"
    worker_log = job_path / "worker.log"

    for restart in range(MAX_RESTARTS + 1):
        status = read_json(job_path / STATUS_FILENAME, {})
        if status.get("state") in TERMINAL_STATES:
            append_log(log_path, f"Terminal state reached: {status.get('state')}.")
            return 0
        if (job_path / STOP_FILENAME).exists():
            append_log(log_path, "Stop request detected. Supervisor exiting.")
            return 0

        append_log(log_path, f"Starting worker process (restart count {restart}).")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"

        with worker_log.open("a", encoding="utf-8") as log_handle:
            result = subprocess.run(
                [sys.executable, str(worker_script), "--job-dir", str(job_path)],
                cwd=str(repo_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                check=False,
            )

        status = read_json(job_path / STATUS_FILENAME, {})
        if status.get("state") in TERMINAL_STATES:
            append_log(
                log_path,
                f"Worker exited with code {result.returncode}; state={status.get('state')}.",
            )
            return 0

        if (job_path / STOP_FILENAME).exists():
            append_log(log_path, "Worker exited and stop request is present.")
            return 0

        if restart >= MAX_RESTARTS:
            append_log(log_path, "Maximum supervisor restart count reached.")
            return 3

        delay = min(60, 5 + restart * 5)
        append_log(
            log_path,
            f"Worker exited unexpectedly with code {result.returncode}. "
            f"Restarting in {delay}s; completed lesson files will be skipped.",
        )
        time.sleep(delay)

    return 3


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-dir", required=True)
    args = parser.parse_args()
    raise SystemExit(main(args.job_dir))
