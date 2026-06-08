from __future__ import annotations

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Iterable

from sraf.models import ChatMessage


class LLMClient(ABC):
    @abstractmethod
    def complete(
        self,
        messages: list[ChatMessage],
        functions: list[dict[str, Any]] | None = None,
    ) -> ChatMessage:
        """Return the next assistant message."""


class GigaChatClient(LLMClient):
    """Small adapter around the official GigaChat SDK.

    The adapter keeps SDK-specific imports lazy so the package can be tested
    without credentials or the provider installed.
    """

    # Max tokens the model can handle (context + response).
    # GigaChat base: 4096, GigaChat-Pro: 8192, GigaChat-Max: 32768
    _MODEL_LIMITS: dict[str, int] = {
        "GigaChat": 4096,
        "GigaChat-2": 8192,
        "GigaChat-Pro": 8192,
        "GigaChat-2-Pro": 8192,
        "GigaChat-Max": 32768,
        "GigaChat-2-Max": 32768,
        "GigaChat-Plus": 8192,
    }

    def __init__(
        self,
        credentials: str | None = None,
        *,
        model: str | None = None,
        scope: str | None = None,
        base_url: str | None = None,
        verify_ssl_certs: bool | None = None,
        max_tokens: int = 1024,
    ) -> None:
        self.credentials = credentials or os.getenv("GIGACHAT_CREDENTIALS") or os.getenv("JPY_API_TOKEN")
        if not self.credentials:
            raise RuntimeError("GIGACHAT_CREDENTIALS (or JPY_API_TOKEN) is required")
        self.model = model or os.getenv("GIGACHAT_MODEL", "GigaChat-2-Pro")
        self.scope = scope or os.getenv("GIGACHAT_SCOPE")
        self.base_url = base_url or os.getenv("GIGACHAT_BASE_URL") or os.getenv("GIGACHST_API_URL")
        self.verify_ssl_certs = verify_ssl_certs
        if self.verify_ssl_certs is None:
            env_verify = os.getenv("GIGACHAT_VERIFY_SSL_CERTS")
            if env_verify is not None:
                self.verify_ssl_certs = env_verify.lower() not in ("false", "0", "no")
        self.max_tokens = max_tokens
        self._max_context = self._MODEL_LIMITS.get(self.model, 4096)

    def _estimate_tokens(self, messages: list[ChatMessage], functions: list[dict] | None) -> int:
        """Rough token estimation. Not exact, but good enough to avoid 413 errors."""
        total = 0
        for msg in messages:
            if msg.content:
                total += len(msg.content) // 2  # ~2 chars per token for Russian text
            if msg.function_call:
                total += len(str(msg.function_call)) // 3
            total += 10  # overhead per message
        if functions:
            total += len(str(functions)) // 3
        return total + self.max_tokens  # reserve space for response

    def complete(
        self,
        messages: list[ChatMessage],
        functions: list[dict[str, Any]] | None = None,
    ) -> ChatMessage:
        try:
            from gigachat import GigaChat
            from gigachat.models import Chat, Messages, MessagesRole
        except ImportError as exc:
            raise RuntimeError("Install the gigachat package to use GigaChatClient") from exc

        # Estimate tokens and truncate if needed
        estimated = self._estimate_tokens(messages, functions)
        if estimated > self._max_context:
            # Truncate conversation history (keep system prompt and last user message)
            keep_system = []
            keep_tail = []
            for msg in messages:
                if msg.role == "system":
                    keep_system.append(msg)
                else:
                    keep_tail.append(msg)
            # Keep only the last 2 messages (user + assistant with function calls)
            keep_tail = keep_tail[-2:] if len(keep_tail) > 2 else keep_tail
            truncated = keep_system + keep_tail
            re_estimated = self._estimate_tokens(truncated, functions)
            if re_estimated < estimated:
                messages = truncated

        payload: dict[str, Any] = {
            "messages": [self._to_sdk_message(message, Messages, MessagesRole) for message in messages],
            "max_tokens": self.max_tokens,
        }
        if self.model:
            payload["model"] = self.model
        if functions:
            payload["functions"] = functions

        chat = Chat(**payload)
        kwargs: dict[str, Any] = {
            "credentials": self.credentials,
        }
        
        if self.base_url:
            kwargs["base_url"] = self.base_url
        
        # Handle SSL verification - when verify_ssl_certs is False,
        # create an SSL context that skips verification
        if self.verify_ssl_certs is False:
            import ssl
            ssl_context = ssl.create_default_context()
            # IMPORTANT: Set check_hostname BEFORE verify_mode
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            kwargs["ssl_context"] = ssl_context
        
        if self.scope:
            kwargs["scope"] = self.scope

        with GigaChat(**kwargs) as client:
            response = client.chat(chat)

        message = response.choices[0].message
        function_call = getattr(message, "function_call", None)
        return ChatMessage(
            role="assistant",
            content=getattr(message, "content", None),
            function_call=self._model_to_dict(function_call) if function_call else None,
        )

    @staticmethod
    def _to_sdk_message(message: ChatMessage, messages_cls: Any, role_cls: Any) -> Any:
        role_name = message.role.upper()
        role = getattr(role_cls, role_name, message.role)
        payload: dict[str, Any] = {"role": role}
        if message.content is not None:
            payload["content"] = message.content
        if message.name:
            payload["name"] = message.name
        if message.function_call:
            payload["function_call"] = message.function_call
        return messages_cls(**payload)

    @staticmethod
    def _model_to_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if hasattr(value, "dict"):
            return value.dict(exclude_none=True)
        if hasattr(value, "model_dump"):
            return value.model_dump(exclude_none=True)
        return json.loads(json.dumps(value, default=lambda item: getattr(item, "__dict__", str(item))))


class ScriptedLLMClient(LLMClient):
    """Deterministic client for tests and demos."""

    def __init__(self, responses: Iterable[ChatMessage | str]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[list[ChatMessage], list[dict[str, Any]] | None]] = []

    def complete(
        self,
        messages: list[ChatMessage],
        functions: list[dict[str, Any]] | None = None,
    ) -> ChatMessage:
        self.calls.append((messages, functions))
        if not self._responses:
            raise RuntimeError("ScriptedLLMClient has no responses left")
        response = self._responses.pop(0)
        if isinstance(response, str):
            return ChatMessage(role="assistant", content=response)
        return response
