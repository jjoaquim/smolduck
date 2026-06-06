"""LLM providers for the AI analyst — Anthropic and Ollama, BYO and pluggable.

The agent's tool-use loop (agent.py) is written against ONE canonical message
shape — the Anthropic block format — so the orchestrator never has to care which
model is behind it:

    messages: [{"role": "user"|"assistant", "content": str | [block, ...]}]
    block:    {"type": "text", "text": ...}
            | {"type": "tool_use", "id": ..., "name": ..., "input": {...}}
            | {"type": "tool_result", "tool_use_id": ..., "name": ..., "content": str}

A provider takes (messages, system, tools) in that shape and returns
`{"stop_reason": str, "blocks": [...]}` with the same block vocabulary. Anthropic
is a near-identity translation; Ollama maps to/from its `/api/chat` schema.

Provider selection (no key is ever persisted; all read from the env at call time):

    SMOLDUCK_LLM_PROVIDER = anthropic | ollama   (explicit; else auto-detected)

Auto-detect picks Anthropic when ANTHROPIC_API_KEY is set, so existing setups are
unchanged. Ollama is opt-in (set the var) because a reachable localhost daemon
isn't a safe thing to assume. Per-provider config:

    Anthropic: ANTHROPIC_API_KEY (required), SMOLDUCK_AGENT_MODEL (default below)
    Ollama:    SMOLDUCK_OLLAMA_HOST (default http://localhost:11434),
               SMOLDUCK_OLLAMA_MODEL (default below) — must be a tool-capable model.

Like the kernel, this code only makes network calls; it never executes untrusted
code, so it is allowed to run in the backend regardless of the VM gate.
"""

from __future__ import annotations

import json
import os

DEFAULT_ANTHROPIC_MODEL = "claude-opus-4-8"
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "llama3.1"
ANTHROPIC_HOST = "api.anthropic.com"

MAX_TOKENS = 2048


class ProviderError(RuntimeError):
    """A provider call failed (transport, auth, or a non-tool-capable model)."""


# --------------------------------------------------------------------- base

class Provider:
    name: str = "base"

    @property
    def model(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def chat(self, messages: list, system: str, tools: list) -> dict:  # pragma: no cover
        raise NotImplementedError


# --------------------------------------------------------------- anthropic

class AnthropicProvider(Provider):
    name = "anthropic"

    @property
    def model(self) -> str:
        return os.environ.get("SMOLDUCK_AGENT_MODEL", DEFAULT_ANTHROPIC_MODEL)

    def chat(self, messages: list, system: str, tools: list) -> dict:
        import anthropic

        client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        try:
            resp = client.messages.create(
                model=self.model, system=system, tools=tools,
                messages=_canonical_to_anthropic(messages), max_tokens=MAX_TOKENS,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI as a message
            raise ProviderError(f"Anthropic request failed: {exc}") from exc

        blocks = []
        for b in resp.content:
            if b.type == "text":
                blocks.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                blocks.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        return {"stop_reason": resp.stop_reason, "blocks": blocks}


def _canonical_to_anthropic(messages: list) -> list:
    """Strip the carried `name` from tool_result blocks (Anthropic keys by id)."""
    out = []
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            content = [{k: v for k, v in b.items() if k != "name"} for b in content]
        out.append({"role": m["role"], "content": content})
    return out


# --------------------------------------------------------------- ollama

class OllamaProvider(Provider):
    name = "ollama"

    def __init__(self) -> None:
        self.host = os.environ.get("SMOLDUCK_OLLAMA_HOST", os.environ.get("OLLAMA_HOST", DEFAULT_OLLAMA_HOST))
        self.host = _normalise_ollama_host(self.host)

    @property
    def model(self) -> str:
        return os.environ.get("SMOLDUCK_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL)

    def chat(self, messages: list, system: str, tools: list) -> dict:
        import httpx

        payload = {
            "model": self.model,
            "messages": _canonical_to_ollama(messages, system),
            "tools": _tools_to_ollama(tools),
            "stream": False,
            "options": {"temperature": 0},
        }
        try:
            resp = httpx.post(f"{self.host}/api/chat", json=payload, timeout=120)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"Ollama request to {self.host} failed: {exc}. "
                f"Is the daemon running and is '{self.model}' a tool-capable model?"
            ) from exc
        return _ollama_to_canonical(data)


def _normalise_ollama_host(host: str) -> str:
    """Trim the trailing slash and rewrite a `localhost` hostname to `127.0.0.1`.

    The two are equivalent on the host, but inside the microVM they are not: the
    guest has no resolver entry for the *name* `localhost` (getaddrinfo fails with
    EAI_NONAME), while smolvm's `--outbound-localhost-only` egress relays the
    guest's `127.0.0.1` straight to the host's loopback. So a host-local daemon is
    reachable by IP but not by name — normalising here makes the documented
    default (`http://localhost:11434`) work both natively and in the VM.
    """
    from urllib.parse import urlsplit, urlunsplit

    host = host.rstrip("/")
    parts = urlsplit(host)
    if parts.hostname == "localhost":
        port = f":{parts.port}" if parts.port else ""
        netloc = f"127.0.0.1{port}"
        host = urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return host


def _tools_to_ollama(tools: list) -> list:
    return [
        {"type": "function", "function": {
            "name": t["name"], "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
        }}
        for t in tools
    ]


def _canonical_to_ollama(messages: list, system: str) -> list:
    """Flatten canonical blocks into Ollama's role-tagged chat messages.

    A user turn that carries tool_results becomes one `tool` message per result;
    an assistant turn's tool_use blocks become `tool_calls` on the assistant
    message. (Ollama matches results to calls positionally, so order is kept.)
    """
    out = [{"role": "system", "content": system}]
    for m in messages:
        role, content = m["role"], m["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "".join(b.get("text", "") for b in content if b["type"] == "text")
            calls = [
                {"function": {"name": b["name"], "arguments": b.get("input") or {}}}
                for b in content if b["type"] == "tool_use"
            ]
            msg = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:  # user turn carrying tool_result blocks
            for b in content:
                if b["type"] == "tool_result":
                    out.append({"role": "tool", "name": b.get("name", ""), "content": b.get("content", "")})
                elif b["type"] == "text":
                    out.append({"role": "user", "content": b["text"]})
    return out


def _ollama_to_canonical(data: dict) -> dict:
    msg = data.get("message", {}) or {}
    blocks = []
    text = msg.get("content") or ""
    if text.strip():
        blocks.append({"type": "text", "text": text})
    for i, call in enumerate(msg.get("tool_calls") or []):
        fn = call.get("function", {}) or {}
        args = fn.get("arguments")
        if isinstance(args, str):  # some builds return a JSON string
            try:
                args = json.loads(args)
            except Exception:  # noqa: BLE001
                args = {}
        blocks.append({"type": "tool_use", "id": f"call_{i}", "name": fn.get("name", ""), "input": args or {}})
    stop = "tool_use" if any(b["type"] == "tool_use" for b in blocks) else "end_turn"
    return {"stop_reason": stop, "blocks": blocks}


# --------------------------------------------------------------- selection

def _selected_name() -> str | None:
    """Which provider the env asks for — explicit var wins, else auto-detect."""
    explicit = (os.environ.get("SMOLDUCK_LLM_PROVIDER") or "").strip().lower()
    if explicit:
        return explicit
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return None


def get_provider() -> Provider | None:
    """The active provider, or None if the analyst is not configured."""
    name = _selected_name()
    if name == "anthropic":
        return AnthropicProvider() if os.environ.get("ANTHROPIC_API_KEY") else None
    if name == "ollama":
        return OllamaProvider()
    return None


def provider_info() -> dict | None:
    p = get_provider()
    return {"provider": p.name, "model": p.model} if p else None


def provider_host(provider: Provider | None) -> str | None:
    """The single host a provider phones (for the egress log/badge), or None."""
    if provider is None:
        return None
    if provider.name == "ollama":
        return getattr(provider, "host", None)
    return ANTHROPIC_HOST


def egress_policy() -> dict:
    """How much network the configured analyst opens in the sandbox.

    Mirrors the CLI's `egressPolicy()`/`analystEgressFlags()` so the UI badge, the
    boot receipt, and the actual smolvm flags can never disagree. Offline whenever
    no provider is configured (or fake mode), since then nothing phones out."""
    if os.environ.get("SMOLDUCK_AGENT_FAKE") == "1":
        return {"policy": "offline", "allowed_hosts": []}
    name = _selected_name()
    if name == "ollama":
        return {"policy": "local-only", "allowed_hosts": [OllamaProvider().host]}
    if name == "anthropic" and os.environ.get("ANTHROPIC_API_KEY"):
        return {"policy": "allow-host", "allowed_hosts": [ANTHROPIC_HOST]}
    return {"policy": "offline", "allowed_hosts": []}
