"""Model layer.

Five providers behind one interface:

* `agent_cli`  - whichever coding-agent CLI you already have signed in: Claude
                 Code, Codex, Gemini CLI, Cursor, OpenCode. No API key and no
                 second subscription - it reuses the sign-in you have. Vendor
                 choice is yours; see llm/agent_cli.py for the table.
* `anthropic`  - the Claude API directly, when you have a key and want lower
                 per-call latency than process startup allows.
* `ollama`     - local models over http://localhost:11434
* `openai`     - any OpenAI-compatible endpoint (vLLM, LM Studio, llama.cpp
                 server, LocalAI, or a hosted API)
* `heuristic`  - no model at all

The heuristic provider is not a toy. It is what makes this product installable:
the sweep, the scoring, the briefing and the approval gate are all deterministic
code, so the system does useful work before anyone downloads a 7B model. The
model layer adds fluency and open-ended planning on top. Everything degrades to
template output rather than failing.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import settings

log = logging.getLogger("duet.llm")


@dataclass
class Completion:
    text: str
    model: str
    provider: str
    tokens_in: int = 0
    tokens_out: int = 0
    degraded: bool = False  # True when we fell back to templates


def resolve_provider(requested: str) -> tuple[str, str]:
    """Turn "auto" into whatever this machine can actually do.

    Order is by how little setup it demands of the person, not by raw quality:
    a signed-in CLI needs nothing, a key needs a billing account, a local server
    needs a download. The heuristic planner is last because it is the only one
    that is not a real model - it keeps the product usable, it does not make it
    good.
    """
    if requested != "auto":
        return requested, f"configured: {requested}"

    from .agent_cli import pick

    chosen = pick(settings.llm_cli)
    if chosen:
        spec, _ = chosen
        return "agent_cli", f"auto-detected {spec.label}"
    if settings.llm_api_key:
        return "anthropic", "auto-detected DUET_LLM_API_KEY"
    try:
        if httpx.get(f"{settings.llm_base_url.rstrip('/')}/api/tags", timeout=1.5).is_success:
            return "ollama", "auto-detected a local Ollama server"
    except Exception:
        pass
    return "heuristic", "no model backend found; using the offline planner"


class LLMClient:
    def __init__(self) -> None:
        self.provider, self.resolution = resolve_provider(settings.llm_provider)
        self.model = settings.llm_model
        self.base_url = settings.llm_base_url.rstrip("/")
        if settings.llm_provider == "auto":
            log.info("model layer: %s", self.resolution)

    # ------------------------------------------------------------------ #
    def complete(
        self,
        prompt: str,
        *,
        system: str | None = None,
        temperature: float | None = None,
        max_tokens: int = 1200,
        fallback: str | None = None,
    ) -> Completion:
        temp = settings.llm_temperature if temperature is None else temperature
        try:
            if self.provider == "agent_cli":
                return self._agent_cli(prompt, system)
            if self.provider == "anthropic":
                return self._anthropic(prompt, system, temp, max_tokens)
            if self.provider == "ollama":
                return self._ollama(prompt, system, temp, max_tokens)
            if self.provider == "openai":
                return self._openai(prompt, system, temp, max_tokens)
        except Exception as exc:  # a dead model server must not take the app down
            log.warning("llm provider %s failed (%s); falling back", self.provider, exc)

        return Completion(
            text=fallback if fallback is not None else "",
            model="heuristic",
            provider="heuristic",
            degraded=self.provider != "heuristic",
        )

    def complete_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        fallback: Any = None,
    ) -> Any:
        """Ask for JSON and parse defensively. Small local models fence output."""
        out = self.complete(
            prompt,
            system=(system or "") + "\nRespond with JSON only. No prose, no code fences.",
            temperature=0.1,
        )
        if not out.text:
            return fallback
        parsed = _extract_json(out.text)
        return parsed if parsed is not None else fallback

    def available(self) -> tuple[bool, str]:
        """Used by the Agent Health dashboard."""
        if self.provider == "heuristic":
            return True, "heuristic (no model server)"
        if self.provider == "agent_cli":
            from .agent_cli import available as cli_available

            return cli_available(settings.llm_cli)
        if self.provider == "anthropic":
            if not settings.llm_api_key:
                return False, "anthropic provider needs DUET_LLM_API_KEY"
            return True, f"Claude API: {self.model}"
        try:
            if self.provider == "ollama":
                r = httpx.get(f"{self.base_url}/api/tags", timeout=4.0)
                r.raise_for_status()
                names = [m.get("name", "") for m in r.json().get("models", [])]
                if self.model in names:
                    return True, f"ollama: {self.model}"
                return False, f"ollama is up but '{self.model}' is not pulled"
            r = httpx.get(f"{self.base_url}/v1/models", timeout=4.0, headers=self._headers())
            r.raise_for_status()
            return True, f"openai-compatible: {self.model}"
        except Exception as exc:
            return False, f"{self.provider} unreachable: {type(exc).__name__}"

    # ------------------------------------------------------------------ #
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if settings.llm_api_key:
            h["Authorization"] = f"Bearer {settings.llm_api_key}"
        return h

    def _agent_cli(self, prompt: str, system: str | None) -> Completion:
        from .agent_cli import complete as cli_complete

        # The CLI owns sampling; temperature is not exposed in headless mode.
        out = cli_complete(
            prompt,
            system=system,
            model=settings.llm_model or None,
            preferred=settings.llm_cli,
            timeout_s=settings.llm_timeout_s,
        )
        if not out.ok:
            raise RuntimeError(out.error or "agent CLI failed")
        return Completion(
            text=out.text,
            model=settings.llm_model or out.cli,
            provider=f"agent_cli:{out.cli}",
        )

    def _anthropic(
        self, prompt: str, system: str | None, temp: float, max_tokens: int
    ) -> Completion:
        """Native Messages API. `system` is a top-level field, not a message."""
        body: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temp,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            body["system"] = system

        r = httpx.post(
            "https://api.anthropic.com/v1/messages",
            json=body,
            headers={
                "content-type": "application/json",
                "x-api-key": settings.llm_api_key or "",
                "anthropic-version": "2023-06-01",
            },
            timeout=settings.llm_timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        text = "".join(
            block.get("text", "") for block in data.get("content", [])
            if block.get("type") == "text"
        )
        usage = data.get("usage", {})
        return Completion(
            text=text.strip(),
            model=data.get("model", self.model),
            provider="anthropic",
            tokens_in=usage.get("input_tokens", 0),
            tokens_out=usage.get("output_tokens", 0),
        )

    def _ollama(self, prompt: str, system: str | None, temp: float, max_tokens: int) -> Completion:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        r = httpx.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": messages,
                "stream": False,
                "options": {"temperature": temp, "num_predict": max_tokens},
            },
            timeout=settings.llm_timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        return Completion(
            text=data.get("message", {}).get("content", "").strip(),
            model=self.model,
            provider="ollama",
            tokens_in=data.get("prompt_eval_count", 0),
            tokens_out=data.get("eval_count", 0),
        )

    def _openai(self, prompt: str, system: str | None, temp: float, max_tokens: int) -> Completion:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        r = httpx.post(
            f"{self.base_url}/v1/chat/completions",
            json={
                "model": self.model,
                "messages": messages,
                "temperature": temp,
                "max_tokens": max_tokens,
            },
            headers=self._headers(),
            timeout=settings.llm_timeout_s,
        )
        r.raise_for_status()
        data = r.json()
        usage = data.get("usage", {})
        return Completion(
            text=data["choices"][0]["message"]["content"].strip(),
            model=data.get("model", self.model),
            provider="openai",
            tokens_in=usage.get("prompt_tokens", 0),
            tokens_out=usage.get("completion_tokens", 0),
        )


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def _extract_json(text: str) -> Any | None:
    text = text.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Last resort: the outermost object or array in the string.
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
