"""The autonomous workflow engine.

A background worker that runs four loops on their own cadences:

  * **task runner**   - picks up queued tasks and executes them (the hot loop)
  * **sweep**         - polls connected apps on `sweep_interval_s`
  * **briefing**      - fires once per user per day at their briefing hour
  * **housekeeping**  - expires stale approvals, retries failed tasks

In production these become Temporal workflows: durable timers, retries and
history for free. The interface here - `WorkflowRun` rows holding `state` and
`next_run_at` - is shaped so that swap is mechanical. What stays the same is
that every unit of work is a persisted row, so a restart mid-flight resumes
rather than losing the task.

Running in-process (a daemon thread) is a deliberate local-first choice: no
broker to install for a laptop deployment.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from ..config import settings
from ..db import session_scope
from ..models import Briefing, Task, TaskStatus, User, WorkflowRun
from . import briefing as briefing_mod
from . import executor, monitor
from .orchestrator import run_task

log = logging.getLogger("duet.scheduler")

TASK_POLL_S = 2.0
HOUSEKEEPING_EVERY_S = 300.0


class Scheduler:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_sweep = 0.0
        self._last_housekeeping = 0.0
        self.started_at: datetime | None = None
        self.stats: dict[str, Any] = {
            "tasks_run": 0,
            "sweeps": 0,
            "briefings": 0,
            "errors": 0,
            "last_error": None,
        }

    # ------------------------------------------------------------------ #
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.started_at = datetime.now(timezone.utc)
        self._thread = threading.Thread(target=self._loop, name="duet-scheduler", daemon=True)
        self._thread.start()
        log.info("scheduler started (sweep every %ss)", settings.sweep_interval_s)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)
        log.info("scheduler stopped")

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def status(self) -> dict[str, Any]:
        next_sweep = max(
            0,
            int(settings.sweep_interval_s - (time.monotonic() - self._last_sweep)),
        ) if self._last_sweep else 0
        return {
            "running": self.running,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "sweep_interval_s": settings.sweep_interval_s,
            "next_sweep_in_s": next_sweep,
            "briefing_hour": settings.briefing_hour,
            **self.stats,
        }

    # ------------------------------------------------------------------ #
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # the loop must never die
                self.stats["errors"] += 1
                self.stats["last_error"] = f"{type(exc).__name__}: {exc}"
                log.exception("scheduler tick failed")
            self._stop.wait(TASK_POLL_S)

    def _tick(self) -> None:
        self._run_queued_tasks()

        now = time.monotonic()
        if now - self._last_sweep >= settings.sweep_interval_s:
            self._last_sweep = now
            self._run_sweep()

        if now - self._last_housekeeping >= HOUSEKEEPING_EVERY_S:
            self._last_housekeeping = now
            self._housekeeping()

        self._maybe_brief()

    # ------------------------------------------------------------------ #
    def _run_queued_tasks(self) -> None:
        with session_scope() as db:
            queued = list(
                db.execute(
                    select(Task)
                    .where(Task.status == TaskStatus.QUEUED.value)
                    .order_by(Task.created_at.asc())
                    .limit(settings.max_parallel_tasks)
                ).scalars()
            )
            ids = [t.id for t in queued]

        for task_id in ids:
            if self._stop.is_set():
                return
            with session_scope() as db:
                task = db.get(Task, task_id)
                # Another worker may have claimed it between the two sessions.
                if task is None or task.status != TaskStatus.QUEUED.value:
                    continue
                run_task(db, task)
                self.stats["tasks_run"] += 1

    def _run_sweep(self) -> None:
        with session_scope() as db:
            users = list(db.execute(select(User)).scalars())
            for user in users:
                if self._stop.is_set():
                    return
                monitor.sweep_user(db, user)
            self.stats["sweeps"] += 1

    def _maybe_brief(self) -> None:
        """One brief per user per day, at or after their briefing hour."""
        with session_scope() as db:
            users = list(db.execute(select(User).where(User.briefing_optin.is_(True))).scalars())
            for user in users:
                local_hour = monitor._local_hour(user)
                if local_hour < settings.briefing_hour:
                    continue

                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                existing = db.execute(
                    select(Briefing).where(
                        Briefing.user_id == user.id, Briefing.for_date == today
                    )
                ).first()
                if existing:
                    continue

                briefing_mod.generate(db, user)
                self.stats["briefings"] += 1
                log.info("delivered briefing to %s", user.email)

    def _housekeeping(self) -> None:
        with session_scope() as db:
            expired = executor.expire_stale(db)
            if expired:
                log.info("expired %d stale approval(s)", expired)

            # Retry tasks that failed transiently and still have attempts left.
            stuck = list(
                db.execute(
                    select(Task).where(
                        Task.status == TaskStatus.FAILED.value,
                        Task.attempts < settings.task_retry_limit,
                    )
                ).scalars()
            )
            for task in stuck:
                task.status = TaskStatus.QUEUED.value
                task.error = None


# --------------------------------------------------------------------------- #
# Durable workflow helpers - the seam Temporal would replace.
# --------------------------------------------------------------------------- #
def schedule(
    db, user_id: str, workflow_key: str, *, state: dict | None = None, delay_s: int = 0
) -> WorkflowRun:
    run = WorkflowRun(
        user_id=user_id,
        workflow_key=workflow_key,
        state=state or {},
        status="scheduled",
        next_run_at=datetime.now(timezone.utc) + timedelta(seconds=delay_s),
    )
    db.add(run)
    db.flush()
    return run


def due_runs(db, limit: int = 20) -> list[WorkflowRun]:
    return list(
        db.execute(
            select(WorkflowRun)
            .where(
                WorkflowRun.status == "scheduled",
                WorkflowRun.next_run_at <= datetime.now(timezone.utc),
            )
            .order_by(WorkflowRun.next_run_at.asc())
            .limit(limit)
        ).scalars()
    )


scheduler = Scheduler()
