"""Provider-agnostic LLM client."""

from __future__ import annotations

import json
import os
import re
from typing import Any

import requests
from dotenv import load_dotenv


class LLMClient:
    """Dispatch chat requests according to the LLM_PROVIDER environment variable."""

    # Providers a reviewer can plausibly already have a key for. Anthropic
    # and MiniMax speak their own APIs; everything else below is
    # OpenAI-wire-compatible and shares one implementation, differing only
    # in base URL, key, and default model (see OPENAI_COMPATIBLE).
    #
    # ``ollama`` is the outlier worth calling out: it runs a local model
    # and needs no API key at all, so the app is runnable by someone who
    # has no account with any vendor.
    VALID_PROVIDERS = (
        "anthropic",
        "openai",
        "minimax",
        "openrouter",
        "google",
        "groq",
        "deepseek",
        "ollama",
    )
    # Canonical provider -> API key env-var mapping. The CLI derives its
    # own map from this so the two surfaces cannot drift (W28).
    PROVIDER_KEY_ENV = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "minimax": "MINIMAX_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "google": "GOOGLE_API_KEY",
        "groq": "GROQ_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        # Ollama is local and unauthenticated. The env var exists so the
        # key-presence checks have something to read; setup writes the
        # literal "local" into it so a reviewer running Ollama passes the
        # same validation path as everyone else instead of needing a
        # special case in the CLI, the setup script, and the fallback
        # chooser.
        "ollama": "OLLAMA_HOST",
    }

    # base URL + default model for the OpenAI-wire-compatible providers.
    # These all work through ``_chat_openai_compatible``; only these three
    # values differ. Base URLs are overridable per provider via
    # ``<PROVIDER>_BASE_URL`` for self-hosted gateways and non-default
    # Ollama ports.
    OPENAI_COMPATIBLE = {
        "openai": ("https://api.openai.com/v1", "gpt-4.1-mini"),
        "openrouter": ("https://openrouter.ai/api/v1", "moonshotai/kimi-k3"),
        # Google's OpenAI-compatibility endpoint. Flash is the free-tier
        # model most reviewers will already have access to, and it
        # supports the function calling this agent depends on.
        "google": (
            "https://generativelanguage.googleapis.com/v1beta/openai/",
            "gemini-2.5-flash",
        ),
        "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile"),
        "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
        # Local. The model must be pulled first (``ollama pull llama3.1``)
        # and must be one that supports tool calling -- the agent records
        # every fact through an update_state tool call, so a model without
        # tool support degrades to the deterministic parsers only.
        "ollama": ("http://localhost:11434/v1", "llama3.1"),
    }
    # W28 refresh: 'claude-3-5-sonnet-latest' was two generations behind
    # (current stable Sonnet is the 4.5 line); 'moonshotai/kimi-k3' is
    # verified against OpenRouter's live /models list. Each id below
    # resolves on its provider's API surface (verified July 2026):
    #   - anthropic: claude-sonnet-4-5  (Anthropic docs, current Sonnet)
    #   - openai:     gpt-4.1-mini      (OpenAI / OpenRouter, current mini)
    #   - minimax:    MiniMax-M3        (minimax platform docs, current frontier)
    #   - openrouter: moonshotai/kimi-k3 (OpenRouter /models, listed)
    # The OpenAI-compatible providers take their default from
    # OPENAI_COMPATIBLE above so the base URL and the model it was chosen
    # for can never drift apart.
    DEFAULT_MODELS = {
        "anthropic": "claude-sonnet-4-5",
        "minimax": "MiniMax-M3",
        **{name: model for name, (_url, model) in OPENAI_COMPATIBLE.items()},
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
        "LLM_PROVIDER", "LLM_MODEL", "LLM_FALLBACK_PROVIDER",
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
        "MINIMAX_API_KEY", "MINIMAX_BASE_URL",
        "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL",
        "GOOGLE_API_KEY", "GOOGLE_BASE_URL",
        "GROQ_API_KEY", "GROQ_BASE_URL",
        "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL",
        "OLLAMA_HOST", "OLLAMA_BASE_URL",
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
        key = LLMClient.PROVIDER_KEY_ENV.get(provider)
        return bool(key and os.getenv(key))

    @staticmethod
    def _normalize(result: dict, provider_used: str | None = None) -> dict:
        content = result.get("content", "")
        if content is None:
            content = ""
        normalized = {
            "content": LLMClient._strip_thinking_blocks(str(content)),
            "tool_calls": list(result.get("tool_calls") or []),
        }
        if provider_used is not None:
            normalized["provider_used"] = provider_used
        return normalized

    @staticmethod
    def _strip_thinking_blocks(content: str) -> str:
        """Strip ``<think>...</think>`` blocks from model output.

        Some reasoning models (notably MiniMax) return their
        chain-of-thought inline inside the ``content`` field rather
        than as a separate ``reasoning_content`` field. The thinking
        trace is internal to the model — it must never reach the user,
        get logged, or be checked by PII / injection guardrails. Leaving
        it in caused the W37 ``pii_not_echoed`` finding (the model
        echoed the user's address back inside its ``<think>`` block
        while correctly avoiding it in the visible reply). We strip
        every ``<think>...</think>`` block (greedy across newlines,
        case-insensitive on the tag name) and trim the resulting
        whitespace.
        """
        if not content:
            return content
        stripped = re.sub(
            r"<think>.*?</think>",
            "",
            content,
            flags=re.DOTALL | re.IGNORECASE,
        )
        return stripped.strip()

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

    def _base_url(self, provider: str) -> str:
        """Base URL for an OpenAI-compatible provider.

        ``<PROVIDER>_BASE_URL`` overrides the built-in default, which is
        what a self-hosted gateway or a non-default Ollama port needs.
        """
        override = os.getenv(f"{provider.upper()}_BASE_URL", "").strip()
        if override:
            return override
        return self.OPENAI_COMPATIBLE[provider][0]

    def _chat_openai_compatible(
        self,
        provider: str,
        messages: list[dict],
        system: str | None,
        tools: list | None,
    ) -> dict:
        """One implementation for every provider that speaks the OpenAI wire
        format: OpenAI itself, OpenRouter, Google (via its compatibility
        endpoint), Groq, DeepSeek, and a local Ollama server.

        They differ only in base URL, API key, and default model, so they
        share this body rather than four near-identical copies that would
        drift the moment one of them needed a fix.
        """
        from openai import OpenAI

        outgoing = list(messages)
        if system is not None:
            outgoing = [{"role": "system", "content": system}, *outgoing]
        kwargs: dict[str, Any] = {
            "model": self._model(provider),
            "messages": outgoing,
        }
        if tools is not None:
            kwargs["tools"] = self._to_openai_tools(tools)
        # Ollama is unauthenticated; the SDK still requires a non-empty
        # string, so send a placeholder rather than special-casing the
        # call site.
        api_key = os.getenv(self.PROVIDER_KEY_ENV[provider], "") or "local"
        client = OpenAI(api_key=api_key, base_url=self._base_url(provider))
        response = client.chat.completions.create(**kwargs)
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

    # Dispatch entry points. ``chat`` resolves the handler by name
    # (``_chat_<provider>``), so each compatible provider needs a method
    # even though they all share one body.

    def _chat_openai(
        self, messages: list[dict], system: str | None, tools: list | None
    ) -> dict:
        return self._chat_openai_compatible("openai", messages, system, tools)

    def _chat_google(
        self, messages: list[dict], system: str | None, tools: list | None
    ) -> dict:
        return self._chat_openai_compatible("google", messages, system, tools)

    def _chat_groq(
        self, messages: list[dict], system: str | None, tools: list | None
    ) -> dict:
        return self._chat_openai_compatible("groq", messages, system, tools)

    def _chat_deepseek(
        self, messages: list[dict], system: str | None, tools: list | None
    ) -> dict:
        return self._chat_openai_compatible("deepseek", messages, system, tools)

    def _chat_ollama(
        self, messages: list[dict], system: str | None, tools: list | None
    ) -> dict:
        return self._chat_openai_compatible("ollama", messages, system, tools)

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
        # MiniMax reasoning models can return an empty content field with
        # the actual answer in reasoning_content. Without this fallback
        # the agent loop's final reply is sometimes just a single space +
        # (Sources: ...), which fails the universal_invariants check
        # (no outcome term in any reply). Content always wins when
        # present -- reasoning_content is the model's internal
        # chain-of-thought and must NOT replace the user-facing text.
        content = message.get("content") or ""
        if not content.strip():
            # Reasoning-only responses are not user-facing prose. Let the agent deterministic floor render the response.
            content = ""
        tool_calls = []
        for call in message.get("tool_calls") or []:
            function = call.get("function", {})
            arguments = function.get("arguments", "{}")
            try:
                parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            except json.JSONDecodeError:
                parsed = {"raw": arguments}
            tool_calls.append({"name": function.get("name", ""), "input": parsed or {}})
        return {"content": content, "tool_calls": tool_calls}

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
