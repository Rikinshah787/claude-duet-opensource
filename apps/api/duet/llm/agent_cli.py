"""Model access through whichever coding-agent CLI is installed.

The point of this module is that the product is not tied to one vendor. If you
already pay for Claude Code, Codex, Gemini CLI or Cursor, you already have a
model this app can use - no API key, no second subscription, no billing setup.
It uses the sign-in you have.

Each CLI is described by a small spec (how to invoke it headlessly, how to pass
a system prompt, how to select a model). Adding one is a table entry, not code.

The same three security properties apply to every CLI here:

* The prompt goes in on **stdin**, never argv. Command lines are visible to
  other processes and land in shell history; email bodies belong in neither.
* `cwd` is a throwaway directory. These tools read project config and agent
  instructions from the working directory, and inheriting this repo's
  instructions into an assistant summarising someone's mail would be both wrong
  and a quiet injection vector.
* Tool use is disabled wherever the CLI supports it. We want text back, not an
  agent that goes and does things - acting is this system's job, behind its own
  approval gate.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

log = logging.getLogger("duet.llm.agent_cli")

_STRIP_ENV = (
    "DUET_SECRET_KEY",
    "DUET_DATABASE_URL",
    "DUET_VAULT_PATH",
    "DUET_LLM_API_KEY",
)


@dataclass(frozen=True)
class CliSpec:
    """How to drive one agent CLI headlessly."""

    key: str
    label: str
    binaries: tuple[str, ...]
    # Flags that make it answer once and exit, reading the prompt from stdin.
    base_args: tuple[str, ...]
    # Format string taking the system prompt, e.g. ("--append-system-prompt", "{}").
    system_args: tuple[str, ...] = ()
    model_args: tuple[str, ...] = ()
    # Extra paths to probe when the binary is not on PATH.
    extra_paths: tuple[str, ...] = ()
    notes: str = ""


# Ordered by preference. The first one actually installed wins.
SPECS: tuple[CliSpec, ...] = (
    CliSpec(
        key="claude",
        label="Claude Code",
        binaries=("claude",),
        base_args=("-p", "--permission-mode", "plan"),
        system_args=("--append-system-prompt", "{}"),
        model_args=("--model", "{}"),
        extra_paths=(
            r"~\.local\bin\claude.exe",
            r"~\.local\bin\claude.EXE",
            "~/.local/bin/claude",
            r"~\AppData\Roaming\npm\claude.cmd",
        ),
        notes="plan mode keeps it from using tools",
    ),
    CliSpec(
        key="codex",
        label="OpenAI Codex CLI",
        binaries=("codex",),
        # `exec` is Codex's non-interactive mode; sandbox read-only stops it
        # touching the filesystem.
        base_args=("exec", "--sandbox", "read-only", "--skip-git-repo-check"),
        model_args=("--model", "{}"),
        extra_paths=(r"~\AppData\Roaming\npm\codex.cmd", "~/.local/bin/codex"),
        notes="system prompt is prepended to the message; no dedicated flag",
    ),
    CliSpec(
        key="gemini",
        label="Gemini CLI",
        binaries=("gemini",),
        base_args=("--prompt",),
        model_args=("--model", "{}"),
        extra_paths=(r"~\AppData\Roaming\npm\gemini.cmd", "~/.local/bin/gemini"),
        notes="reads the prompt from stdin when --prompt is given no value",
    ),
    CliSpec(
        key="cursor",
        label="Cursor Agent",
        binaries=("cursor-agent",),
        base_args=("-p", "--output-format", "text"),
        model_args=("--model", "{}"),
        extra_paths=("~/.local/bin/cursor-agent",),
    ),
    CliSpec(
        key="opencode",
        label="OpenCode",
        binaries=("opencode",),
        base_args=("run",),
        model_args=("--model", "{}"),
        extra_paths=(r"~\AppData\Roaming\npm\opencode.cmd", "~/.local/bin/opencode"),
    ),
)

BY_KEY: dict[str, CliSpec] = {s.key: s for s in SPECS}


@dataclass
class CliResult:
    text: str
    ok: bool
    cli: str = ""
    error: str | None = None


def _locate(spec: CliSpec) -> str | None:
    for name in spec.binaries:
        found = shutil.which(name)
        if found:
            return found
    for raw in spec.extra_paths:
        path = os.path.expanduser(raw)
        if os.path.exists(path):
            return path
    return None


def detect() -> list[tuple[CliSpec, str]]:
    """Every agent CLI installed on this machine, in preference order."""
    out: list[tuple[CliSpec, str]] = []
    for spec in SPECS:
        path = _locate(spec)
        if path:
            out.append((spec, path))
    return out


def pick(preferred: str | None = None) -> tuple[CliSpec, str] | None:
    """Resolve the CLI to use. `preferred` names one explicitly."""
    found = detect()
    if not found:
        return None
    if preferred and preferred != "auto":
        for spec, path in found:
            if spec.key == preferred:
                return spec, path
        return None  # asked for one we do not have: fail loudly, don't substitute
    return found[0]


def available(preferred: str | None = None) -> tuple[bool, str]:
    chosen = pick(preferred)
    if chosen:
        spec, path = chosen
        return True, f"{spec.label} ({path})"
    installed = ", ".join(s.label for s, _ in detect()) or "none"
    if preferred and preferred != "auto":
        return False, f"{preferred} CLI not found (installed: {installed})"
    return False, "no agent CLI found (tried: " + ", ".join(s.key for s in SPECS) + ")"


def _child_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV}
    env["CI"] = "1"
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    return env


def complete(
    prompt: str,
    *,
    system: str | None = None,
    model: str | None = None,
    preferred: str | None = None,
    timeout_s: float = 180.0,
) -> CliResult:
    """One prompt in, one answer out, through whichever CLI is available."""
    chosen = pick(preferred)
    if not chosen:
        ok, detail = available(preferred)
        return CliResult("", ok=False, error=detail)

    spec, path = chosen
    cmd = [path, *spec.base_args]

    text = prompt
    if system:
        if spec.system_args:
            cmd += [a.format(system) for a in spec.system_args]
        else:
            # No dedicated flag: fold it into the message. Marked clearly so the
            # model can still tell instructions from the data below them.
            text = f"<instructions>\n{system}\n</instructions>\n\n{prompt}"

    if model and spec.model_args:
        cmd += [a.format(model) for a in spec.model_args]

    # Not a context manager: on Windows the CLI can still hold a handle to the
    # directory when the call returns, and the delete then raises *after* a
    # perfectly good answer. Cleanup is best-effort.
    workdir = tempfile.mkdtemp(prefix="duet-llm-")
    try:
        proc = subprocess.run(
            cmd,
            input=text,            # stdin, never argv
            cwd=workdir,
            env=_child_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        return CliResult("", ok=False, cli=spec.key,
                         error=f"{spec.label} timed out after {timeout_s}s")
    except Exception as exc:
        return CliResult("", ok=False, cli=spec.key,
                         error=f"{type(exc).__name__}: {exc}")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:400]
        return CliResult("", ok=False, cli=spec.key,
                         error=f"{spec.label} exited {proc.returncode}: {detail}")

    return CliResult((proc.stdout or "").strip(), ok=True, cli=spec.key)
