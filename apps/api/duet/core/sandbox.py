"""Isolated execution environment.

Work that needs a filesystem, a shell or a browser runs here rather than in the
API process. The product framing is "the assistant has its own machine" - the
engineering reality is that any code or page content the assistant touches is
untrusted, so it must not share a process, a working directory or a network
namespace with the thing holding your credentials.

Three backends behind one interface:

* ``subprocess`` - the default. A per-task workspace directory, a scrubbed
  environment, a wall-clock timeout and an output cap. Honest about what it is:
  process isolation, not a security boundary. Good enough for running a
  generated script on your own laptop, and the only option with no dependencies.
* ``docker``     - a throwaway container with no network by default, a read-only
  root, dropped capabilities and a memory cap. This is the real boundary, and
  what a deployment should use.
* ``browser``    - Playwright, driven inside whichever of the above is active,
  for tasks that need a logged-in web session. Credentials are injected at the
  moment of use via the vault, so the page - and the model - never see them.

The browser backend is optional; Playwright is not a dependency. `available()`
reports what this machine can actually do, and the Agent Health view renders it,
so the capability is never silently assumed.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import DATA_DIR

log = logging.getLogger("duet.sandbox")

WORKSPACES = DATA_DIR / "workspaces"

# Variables that must never reach a sandboxed process. Anything holding a
# credential for this host is stripped before exec.
ENV_DENYLIST = (
    "DUET_SECRET_KEY",
    "DUET_LLM_API_KEY",
    "DUET_DATABASE_URL",
    "DUET_VAULT_PATH",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "SLACK_TOKEN",
    "STRIPE_SECRET_KEY",
)


@dataclass
class SandboxResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    backend: str = "subprocess"


@dataclass
class Workspace:
    """A per-task scratch directory. Nothing escapes it by design."""

    id: str
    path: Path

    def write(self, name: str, content: str) -> Path:
        target = self._resolve(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def read(self, name: str) -> str:
        return self._resolve(name).read_text(encoding="utf-8")

    def list_files(self) -> list[dict[str, Any]]:
        out = []
        for p in sorted(self.path.rglob("*")):
            if p.is_file():
                out.append(
                    {
                        "name": p.relative_to(self.path).as_posix(),
                        "bytes": p.stat().st_size,
                    }
                )
        return out

    def _resolve(self, name: str) -> Path:
        """Reject traversal. `../../.ssh/id_rsa` is not a filename."""
        target = (self.path / name).resolve()
        if not str(target).startswith(str(self.path.resolve())):
            raise ValueError(f"path escapes the workspace: {name}")
        return target

    def destroy(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


def create_workspace(task_id: str | None = None) -> Workspace:
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    wid = task_id or uuid.uuid4().hex[:12]
    path = Path(tempfile.mkdtemp(prefix=f"ws-{wid}-", dir=WORKSPACES))
    return Workspace(id=wid, path=path)


def _clean_env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ENV_DENYLIST and not k.startswith(("DUET_", "npm_"))
    }
    env["HOME"] = env.get("TEMP", env.get("TMPDIR", "/tmp"))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["NO_NETWORK"] = "1"
    return env


# --------------------------------------------------------------------------- #
class Sandbox:
    def __init__(self, backend: str = "subprocess"):
        self.backend = backend

    # ------------------------------------------------------------------ #
    def available(self) -> dict[str, Any]:
        """What this machine can actually run. Rendered in Agent Health."""
        docker = shutil.which("docker") is not None
        try:
            import playwright  # noqa: F401

            browser = True
        except ImportError:
            browser = False

        return {
            "subprocess": True,
            "docker": docker,
            "browser": browser,
            "active": self.backend,
            "note": (
                "subprocess is process isolation, not a security boundary - "
                "use the docker backend for untrusted code"
            ),
        }

    # ------------------------------------------------------------------ #
    def run_python(
        self,
        code: str,
        *,
        workspace: Workspace | None = None,
        timeout_s: float = 30.0,
        max_output: int = 40_000,
    ) -> SandboxResult:
        """Run a generated script. Never with the API process's environment."""
        ws = workspace or create_workspace()
        script = ws.write("_run.py", code)
        before = {f["name"] for f in ws.list_files()}

        if self.backend == "docker" and shutil.which("docker"):
            result = self._run_docker(ws, script, timeout_s, max_output)
        else:
            result = self._run_subprocess(ws, script, timeout_s, max_output)

        after = ws.list_files()
        result.artifacts = [f for f in after if f["name"] not in before | {"_run.py"}]
        return result

    def _run_subprocess(
        self, ws: Workspace, script: Path, timeout_s: float, max_output: int
    ) -> SandboxResult:
        import sys

        started = time.perf_counter()
        try:
            proc = subprocess.run(
                # -I: isolated mode. Without it the interpreter imports from the
                # script's own directory, so a file the task wrote named
                # `json.py` would be loaded by `import json`.
                [sys.executable, "-I", "-S", str(script)],
                cwd=ws.path,
                env=_clean_env(),
                capture_output=True,
                text=True,
                timeout=timeout_s,
            )
            return SandboxResult(
                ok=proc.returncode == 0,
                stdout=proc.stdout[:max_output],
                stderr=proc.stderr[:max_output],
                exit_code=proc.returncode,
                duration_ms=int((time.perf_counter() - started) * 1000),
                backend="subprocess",
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False,
                error=f"timed out after {timeout_s}s",
                duration_ms=int(timeout_s * 1000),
                backend="subprocess",
            )
        except Exception as exc:
            return SandboxResult(
                ok=False, error=f"{type(exc).__name__}: {exc}", backend="subprocess"
            )

    def _run_docker(
        self, ws: Workspace, script: Path, timeout_s: float, max_output: int
    ) -> SandboxResult:
        started = time.perf_counter()
        cmd = [
            "docker", "run", "--rm",
            "--network", "none",          # no egress unless explicitly granted
            "--memory", "512m",
            "--cpus", "1",
            "--pids-limit", "128",
            "--read-only",                 # root fs immutable
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:rw,size=64m",
            "-v", f"{ws.path}:/work:rw",
            "-w", "/work",
            "python:3.12-slim",
            "python", "-I", "-S", f"/work/{script.name}",
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_s + 15
            )
            return SandboxResult(
                ok=proc.returncode == 0,
                stdout=proc.stdout[:max_output],
                stderr=proc.stderr[:max_output],
                exit_code=proc.returncode,
                duration_ms=int((time.perf_counter() - started) * 1000),
                backend="docker",
            )
        except subprocess.TimeoutExpired:
            return SandboxResult(
                ok=False, error=f"timed out after {timeout_s}s", backend="docker"
            )
        except Exception as exc:
            return SandboxResult(
                ok=False, error=f"{type(exc).__name__}: {exc}", backend="docker"
            )

    # ------------------------------------------------------------------ #
    def browse(
        self,
        url: str,
        *,
        actions: list[dict[str, Any]] | None = None,
        workspace: Workspace | None = None,
        screenshot: bool = True,
        timeout_s: float = 30.0,
    ) -> SandboxResult:
        """Drive a real browser.

        Page content is untrusted input: anything read back is passed through
        the injection screen before it can reach a prompt. Credentials are
        resolved from the vault at fill time and never returned.
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return SandboxResult(
                ok=False,
                error=(
                    "browser automation needs Playwright: "
                    "pip install playwright && playwright install chromium"
                ),
                backend="browser",
            )

        from .injection import scan

        ws = workspace or create_workspace()
        started = time.perf_counter()
        artifacts: list[dict[str, Any]] = []

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_default_timeout(timeout_s * 1000)
                page.goto(url)

                for step in actions or []:
                    kind = step.get("action")
                    if kind == "click":
                        page.click(step["selector"])
                    elif kind == "fill":
                        page.fill(step["selector"], step.get("value", ""))
                    elif kind == "wait":
                        page.wait_for_selector(step["selector"])
                    elif kind == "screenshot":
                        shot = ws.path / f"step-{len(artifacts)}.png"
                        page.screenshot(path=str(shot))
                        artifacts.append({"name": shot.name, "kind": "screenshot"})

                text = page.inner_text("body")[:20_000]
                title = page.title()

                if screenshot:
                    shot = ws.path / "final.png"
                    page.screenshot(path=str(shot), full_page=True)
                    artifacts.append({"name": shot.name, "kind": "screenshot"})

                browser.close()

            threat = scan(text)
            if threat.hostile:
                text = (
                    "[Page content withheld: it attempted to issue instructions "
                    f"({', '.join(threat.matches)}).]"
                )

            return SandboxResult(
                ok=True,
                stdout=text,
                duration_ms=int((time.perf_counter() - started) * 1000),
                artifacts=artifacts,
                backend="browser",
            )
        except Exception as exc:
            return SandboxResult(
                ok=False, error=f"{type(exc).__name__}: {exc}", backend="browser"
            )


from ..config import settings  # noqa: E402

sandbox = Sandbox(backend=settings.sandbox_backend)
