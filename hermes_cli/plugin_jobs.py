"""Bounded, profile-local async job handoff for hosted plugin APIs.

The broker lives in one profile's plugin-host process. API routes see only the
broker bound to their own plugin, so a request cannot select another plugin's
handler or profile.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Optional

MAX_PAYLOAD_BYTES = 64 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_ACTIVE_JOBS = 4
JOB_TTL_SECONDS = 24 * 60 * 60


@dataclass
class _Job:
    id: str
    created_at: float
    status: str = "queued"
    result: Any = None
    error: Optional[str] = None
    task: Optional[asyncio.Task] = field(default=None, repr=False)


class PluginJobBroker:
    """In-memory job broker owned by one plugin host and scoped to one plugin."""

    def __init__(self, owner: str, loop: asyncio.AbstractEventLoop):
        self.owner = owner
        self.loop = loop
        self._handlers: Dict[str, Callable[[dict], Any]] = {}
        self._jobs: Dict[str, _Job] = {}

    def register(self, key: str, handler: Callable[[dict], Any]) -> None:
        if not isinstance(key, str) or not key or len(key) > 64 or not key.replace("_", "").isalnum():
            raise ValueError("job handler key must be 1-64 alphanumeric characters or underscores")
        if not callable(handler):
            raise TypeError("job handler must be callable")
        if key in self._handlers:
            raise ValueError(f"job handler {key!r} is already registered")
        self._handlers[key] = handler

    async def submit(self, key: str, payload: dict) -> dict:
        self._prune()
        handler = self._handlers.get(key)
        if handler is None:
            raise LookupError("job handler is unavailable")
        if not isinstance(payload, dict):
            raise ValueError("job payload must be an object")
        try:
            encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("job payload must contain JSON values only") from exc
        if len(encoded) > MAX_PAYLOAD_BYTES:
            raise ValueError("job payload exceeds 64 KiB")
        active = sum(job.status in {"queued", "running"} for job in self._jobs.values())
        if active >= MAX_ACTIVE_JOBS:
            raise RuntimeError("plugin job queue is full")
        job = _Job(id=secrets.token_urlsafe(18), created_at=time.time())
        self._jobs[job.id] = job
        job.task = self.loop.create_task(self._run(job, handler, json.loads(encoded)))
        return self._view(job)

    async def _run(self, job: _Job, handler: Callable[[dict], Any], payload: dict) -> None:
        job.status = "running"
        try:
            result = handler(payload)
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                result = await result
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False).encode("utf-8")
            if len(encoded) > MAX_RESULT_BYTES:
                raise ValueError("job result exceeds 64 KiB")
            job.result = json.loads(encoded)
            job.status = "completed"
        except asyncio.CancelledError:
            job.status = "failed"
            job.error = "job cancelled"
            raise
        except Exception as exc:
            # Do not return exception text, which can contain prompts, provider data or secrets.
            job.status = "failed"
            job.error = type(exc).__name__

    async def get(self, job_id: str) -> Optional[dict]:
        self._prune()
        job = self._jobs.get(job_id)
        return self._view(job) if job else None

    def close(self) -> None:
        for job in self._jobs.values():
            if job.task is not None and not job.task.done():
                job.task.cancel()
        self._handlers.clear()

    def _prune(self) -> None:
        cutoff = time.time() - JOB_TTL_SECONDS
        for job_id, job in list(self._jobs.items()):
            if job.created_at < cutoff and job.status not in {"queued", "running"}:
                self._jobs.pop(job_id, None)

    @staticmethod
    def _view(job: _Job) -> dict:
        return {"id": job.id, "status": job.status, "result": job.result, "error": job.error}


class PluginJobs:
    """API-facing facade bound to one plugin; callers never supply an owner/profile."""

    def __init__(self, broker: PluginJobBroker):
        self._broker = broker

    async def submit(self, key: str, payload: dict) -> dict:
        return await self._broker.submit(key, payload)

    async def get(self, job_id: str) -> Optional[dict]:
        return await self._broker.get(job_id)
