# -*- coding: utf-8 -*-
"""Persistent asynchronous jobs for admin tests and data refreshes."""

import os
import secrets
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from admin_common import ApiError, now_iso
from aicm_io import read_json, write_json_atomic


ACTIVE_STATES = {"queued", "running"}
TERMINAL_STATES = {"succeeded", "failed", "skipped", "interrupted"}
JOB_KINDS = {"live", "history", "interface_test"}
MAX_JOBS = 500
PAGE_SIZES = {20, 50, 100}


class AdminJobManager:
    def __init__(self, base, python_executable=None, runner=None):
        self.base = Path(base)
        self.path = self.base / "data" / "admin_jobs.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.python_executable = str(
            python_executable or self.base / ".venv" / "bin" / "python"
        )
        self.runner = runner
        self._lock = threading.RLock()
        self._reconcile_interrupted()

    def _payload(self):
        payload = read_json(self.path, {"version": 1, "jobs": []})
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            return {"version": 1, "jobs": []}
        return payload

    def _save(self, payload):
        payload["version"] = 1
        payload["jobs"] = payload.get("jobs", [])[-MAX_JOBS:]
        write_json_atomic(self.path, payload)

    def _reconcile_interrupted(self):
        with self._lock:
            payload = self._payload()
            changed = False
            for job in payload["jobs"]:
                if job.get("status") in ACTIVE_STATES:
                    job.update({
                        "status": "interrupted",
                        "finished_at": now_iso(),
                        "error": "service restarted while job was active",
                        "stage": "interrupted",
                    })
                    changed = True
            if changed:
                self._save(payload)

    def _find(self, payload, job_id):
        for job in payload.get("jobs", []):
            if job.get("job_id") == job_id:
                return job
        return None

    def _update(self, job_id, **fields):
        with self._lock:
            payload = self._payload()
            job = self._find(payload, job_id)
            if not job:
                raise ApiError(404, "job_not_found", "Admin job was not found")
            job.update(fields)
            self._save(payload)
            return dict(job)

    def _finish(self, job_id, status, result=None, error="", log_tail=None):
        if status not in TERMINAL_STATES:
            raise ValueError(f"invalid terminal job state: {status}")
        job = self.get(job_id)
        elapsed = max(0.0, time.time() - float(job.get("started_epoch") or time.time()))
        return self._update(
            job_id,
            status=status,
            stage=status,
            finished_at=now_iso(),
            elapsed_s=round(elapsed, 2),
            result=result or {},
            error=str(error or "")[:1000],
            log_tail=list(log_tail or [])[-200:],
        )

    def start(self, kind, trigger="manual", payload=None):
        kind = str(kind or "").strip().lower()
        if kind not in JOB_KINDS:
            raise ApiError(400, "invalid_job_kind", "Job kind must be live, history, or interface_test")
        with self._lock:
            registry = self._payload()
            for job in reversed(registry["jobs"]):
                if job.get("kind") == kind and job.get("status") in ACTIVE_STATES:
                    return {**job, "already_running": True}
            now = time.time()
            job = {
                "job_id": secrets.token_hex(12),
                "kind": kind,
                "trigger": str(trigger or "manual"),
                "status": "queued",
                "stage": "queued",
                "created_at": now_iso(),
                "started_at": None,
                "finished_at": None,
                "started_epoch": now,
                "elapsed_s": 0,
                "payload": payload or {},
                "result": {},
                "error": "",
                "log_tail": [],
            }
            registry["jobs"].append(job)
            self._save(registry)

        self._update(job["job_id"], status="running", stage="starting", started_at=now_iso())
        done = lambda status, result=None, error="", log_tail=None: self._finish(
            job["job_id"], status, result, error, log_tail
        )
        if self.runner is not None:
            self.runner(dict(job), done)
        else:
            thread = threading.Thread(
                target=self._run_default,
                args=(job["job_id"],),
                name=f"aicm-admin-{kind}-{job['job_id'][:6]}",
                daemon=True,
            )
            thread.start()
        return {**self.get(job["job_id"]), "already_running": False}

    def _run_default(self, job_id):
        job = self.get(job_id)
        kind = job["kind"]
        if kind == "interface_test":
            try:
                from admin_service import test_interfaces
                result = test_interfaces(
                    self.base,
                    interface_ids=job.get("payload", {}).get("interface_ids"),
                )
                self._finish(job_id, "succeeded", result=result)
            except Exception as exc:
                self._finish(job_id, "failed", error=str(exc))
            return

        script = "fetch_live.py" if kind == "live" else "fetch_data.py"
        self._update(job_id, stage="running_script")
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        lines = []
        try:
            process = subprocess.Popen(
                [self.python_executable, script],
                cwd=str(self.base),
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            for line in process.stdout or []:
                lines.append(line.rstrip())
                lines = lines[-200:]
            return_code = process.wait()
            joined = "\n".join(lines).lower()
            if return_code == 0 and "skipped: already running" in joined:
                self._finish(job_id, "skipped", error="matching refresh is already running", log_tail=lines)
            elif return_code == 0:
                status_name = "live_status.json" if kind == "live" else "history_status.json"
                result = read_json(self.base / "data" / status_name, {})
                self._finish(job_id, "succeeded", result=result, log_tail=lines)
            else:
                self._finish(job_id, "failed", error=lines[-1] if lines else f"exit code {return_code}", log_tail=lines)
        except Exception as exc:
            self._finish(job_id, "failed", error=str(exc), log_tail=lines)

    def get(self, job_id):
        with self._lock:
            job = self._find(self._payload(), str(job_id))
            if not job:
                raise ApiError(404, "job_not_found", "Admin job was not found")
            return dict(job)

    def list(self, params=None):
        params = params or {}
        with self._lock:
            jobs = [dict(job) for job in reversed(self._payload()["jobs"])]
        kind = str(params.get("kind") or "").strip().lower()
        status = str(params.get("status") or "").strip().lower()
        if kind:
            jobs = [job for job in jobs if job.get("kind") == kind]
        if status:
            jobs = [job for job in jobs if job.get("status") == status]
        try:
            page = max(1, int(params.get("page") or 1))
            page_size = int(params.get("page_size") or 50)
        except (TypeError, ValueError):
            page, page_size = 1, 50
        if page_size not in PAGE_SIZES:
            page_size = 50
        total = len(jobs)
        start = (page - 1) * page_size
        return {
            "items": jobs[start:start + page_size],
            "page": page,
            "page_size": page_size,
            "total": total,
            "facets": {
                "kinds": dict(Counter(job.get("kind") for job in jobs)),
                "statuses": dict(Counter(job.get("status") for job in jobs)),
            },
        }
