"""Provider-agnostic LLM client."""

from __future__ import annotations

import json
import os
from typing import Any

import requests
from dotenv import load_dotenv


class LLMClient:
    """Dispatch chat requests according to the LLM_PROVIDER environment variable."""

    VALID_PROVIDERS = ("anthropic", "openai", "minimax")
    DEFAULT_MODELS = {
        "anthropic": "claude-3-5-sonnet-latest",
        "openai": "gpt-4o-mini",
        "minimax": "MiniMax-Text-01",
    }

    def __init__(self) -> None:
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
        result = getattr(self, f"_chat_{provider}")(messages, system, tools)
        return self._normalize(result)

    @staticmethod
    def _normalize(result: dict) -> dict:
        content = result.get("content", "")
        if content is None:
            content = ""
        return {
            "content": str(content),
            "tool_calls": list(result.get("tool_calls") or []),
        }

    def _model(self, provider: str) -> str:
        return os.getenv("LLM_MODEL", "").strip() or self.DEFAULT_MODELS[provider]

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
            kwargs["tools"] = tools
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
            payload["tools"] = tools
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
