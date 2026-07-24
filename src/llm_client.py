"""Provider-agnostic LLM client."""

from __future__ import annotations

import json
import os
from typing import Any

import requests
from dotenv import load_dotenv


class LLMClient:
    """Dispatch chat requests according to the LLM_PROVIDER environment variable."""

    VALID_PROVIDERS = ("anthropic", "openai", "minimax", "openrouter")
    DEFAULT_MODELS = {
        "anthropic": "claude-3-5-sonnet-latest",
        "openai": "gpt-4o-mini",
        "minimax": "MiniMax-Text-01",
        "openrouter": "moonshotai/kimi-k3",
    }

    # Keys .env is allowed to fill in even when already present in the
    # environment, but only if the existing value is empty. python-dotenv's
    # default load_dotenv() never overrides an already-set var - including
    # one set to "" - so a stray empty export (e.g. MINIMAX_API_KEY=""
    # pre-exported in the shell environment) silently
    # shadows the real value in .env. override=True would fix that but also
    # stomps deliberate test-time monkeypatch.setenv() calls, so we only
    # clear the specific empty-string case before the normal (non-override)
    # load.
    _ENV_KEYS = (
        "LLM_PROVIDER", "LLM_MODEL",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
        "MINIMAX_API_KEY", "MINIMAX_BASE_URL",
        "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
    )

    def __init__(self) -> None:
        for key in self._ENV_KEYS:
            if os.environ.get(key) == "":
                del os.environ[key]
        load_dotenv()

    def chat(
        self,
        messages: list[dict],
        system: str | None = None,
        tools: list | None = None,
    ) -> dict:
        provider = os.getenv("LLM_PROVIDER", "").strip().lower()
        if provider not in self.VALID_PROVIDERS:
            valid = ", ".join(self.VALID_PROVIDERS)
            raise ValueError(
                f"Invalid or missing LLM_PROVIDER {provider!r}; valid options are: {valid}"
            )
        try:
            result = getattr(self, f"_chat_{provider}")(messages, system, tools)
            return self._normalize(result, provider_used=provider)
        except Exception as primary_exc:
            fallback = self._fallback_provider(provider)
            if fallback is None:
                raise
            try:
                result = getattr(self, f"_chat_{fallback}")(messages, system, tools)
            except Exception:
                # Fallback also failed (or raised). Re-raise the ORIGINAL
                # primary exception so callers see the same error they'd see
                # without a fallback configured. Fallback failures are
                # operational noise here, not the signal.
                raise primary_exc
            return self._normalize(result, provider_used=fallback)

    def _fallback_provider(self, primary: str) -> str | None:
        """Return the configured fallback provider, or None.

        Validation rules:
        - env var unset / empty -> None (fallback disabled, default behavior)
        - value not in VALID_PROVIDERS -> ignore (treat as unset; don't
          shadow the primary exception with a config typo)
        - value equals primary -> ignore (must differ)
        - value's API key is missing -> ignore (no point retrying without
          credentials; would just re-raise with a different error)
        """
        raw = os.getenv("LLM_FALLBACK_PROVIDER", "").strip().lower()
        if not raw:
            return None
        if raw not in self.VALID_PROVIDERS:
            return None
        if raw == primary:
            return None
        if not self._has_api_key(raw):
            return None
        return raw

    @staticmethod
    def _has_api_key(provider: str) -> bool:
        key = {
            "anthropic": "ANTHROPIC_API_KEY",
            "openai": "OPENAI_API_KEY",
            "minimax": "MINIMAX_API_KEY",
            "openrouter": "OPENROUTER_API_KEY",
        }.get(provider)
        return bool(key and os.getenv(key))

    @staticmethod
    def _normalize(result: dict, provider_used: str | None = None) -> dict:
        content = result.get("content", "")
        if content is None:
            content = ""
        normalized = {
            "content": str(content),
            "tool_calls": list(result.get("tool_calls") or []),
        }
        if provider_used is not None:
            normalized["provider_used"] = provider_used
        return normalized

    def _model(self, provider: str) -> str:
        return os.getenv("LLM_MODEL", "").strip() or self.DEFAULT_MODELS[provider]

    @staticmethod
    def _to_openai_tools(tools: list | None) -> list | None:
        """Translate Anthropic-shaped tool defs (name/description/input_schema)
        into the OpenAI/MiniMax-compatible shape (type=function wrapper,
        parameters instead of input_schema). Tool defs are authored once in
        agent.py using the Anthropic shape; every non-Anthropic provider needs
        this translation or the API rejects the request outright."""
        if tools is None:
            return None
        converted = []
        for t in tools:
            if "function" in t and t.get("type") == "function":
                converted.append(t)  # already OpenAI-shaped
                continue
            converted.append(
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
                    },
                }
            )
        return converted

    def _chat_anthropic(
        self, messages: list[dict], system: str | None, tools: list | None
    ) -> dict:
        import anthropic

        kwargs: dict[str, Any] = {
            "model": self._model("anthropic"),
            "max_tokens": 4096,
            "messages": messages,
        }
        if system is not None:
            kwargs["system"] = system
        if tools is not None:
            kwargs["tools"] = tools
        response = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY")).messages.create(
            **kwargs
        )

        text_parts: list[str] = []
        tool_calls: list[dict] = []
        for block in response.content:
            block_type = getattr(block, "type", None)
            if block_type == "text":
                text_parts.append(getattr(block, "text", ""))
            elif block_type == "tool_use":
                tool_calls.append(
                    {
                        "name": getattr(block, "name", ""),
                        "input": getattr(block, "input", {}) or {},
                    }
                )
        return {"content": "".join(text_parts), "tool_calls": tool_calls}

    def _chat_openai(
        self, messages: list[dict], system: str | None, tools: list | None
    ) -> dict:
        from openai import OpenAI

        outgoing = list(messages)
        if system is not None:
            outgoing = [{"role": "system", "content": system}, *outgoing]
        kwargs: dict[str, Any] = {
            "model": self._model("openai"),
            "messages": outgoing,
        }
        if tools is not None:
            kwargs["tools"] = self._to_openai_tools(tools)
        response = OpenAI(api_key=os.getenv("OPENAI_API_KEY")).chat.completions.create(
            **kwargs
        )
        message = response.choices[0].message
        tool_calls = []
        for call in getattr(message, "tool_calls", None) or []:
            arguments = getattr(call.function, "arguments", "{}")
            try:
                parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError:
                parsed = {"raw": arguments}
            tool_calls.append({"name": call.function.name, "input": parsed or {}})
        return {"content": message.content or "", "tool_calls": tool_calls}

    def _chat_minimax(
        self, messages: list[dict], system: str | None, tools: list | None
    ) -> dict:
        outgoing = list(messages)
        if system is not None:
            outgoing = [{"role": "system", "content": system}, *outgoing]
        payload: dict[str, Any] = {
            "model": self._model("minimax"),
            "messages": outgoing,
        }
        if tools is not None:
            payload["tools"] = self._to_openai_tools(tools)
        base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1").rstrip("/")
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('MINIMAX_API_KEY', '')}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        tool_calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            arguments = function.get("arguments", "{}")
            try:
                parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError:
                parsed = {"raw": arguments}
            tool_calls.append({"name": function.get("name", ""), "input": parsed or {}})
        return {"content": message.get("content") or "", "tool_calls": tool_calls}

    def _chat_openrouter(
        self, messages: list[dict], system: str | None, tools: list | None
    ) -> dict:
        outgoing = list(messages)
        if system is not None:
            outgoing = [{"role": "system", "content": system}, *outgoing]
        payload: dict[str, Any] = {
            "model": self._model("openrouter"),
            "messages": outgoing,
        }
        if tools is not None:
            payload["tools"] = self._to_openai_tools(tools)
        base_url = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")
        response = requests.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY', '')}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=120,
        )
        response.raise_for_status()
        message = response.json()["choices"][0]["message"]
        tool_calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            arguments = function.get("arguments", "{}")
            try:
                parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError:
                parsed = {"raw": arguments}
            tool_calls.append({"name": function.get("name", ""), "input": parsed or {}})
        return {"content": message.get("content") or "", "tool_calls": tool_calls}
