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

    def __init__(
        self,
        credentials: str | None = None,
        *,
        model: str | None = None,
        scope: str | None = None,
        verify_ssl_certs: bool | None = None,
    ) -> None:
        self.credentials = credentials or os.getenv("GIGACHAT_CREDENTIALS")
        if not self.credentials:
            raise RuntimeError("GIGACHAT_CREDENTIALS is required")
        self.model = model or os.getenv("GIGACHAT_MODEL")
        self.scope = scope or os.getenv("GIGACHAT_SCOPE")
        self.verify_ssl_certs = verify_ssl_certs

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

        payload: dict[str, Any] = {
            "messages": [self._to_sdk_message(message, Messages, MessagesRole) for message in messages],
        }
        if self.model:
            payload["model"] = self.model
        if functions:
            payload["functions"] = functions

        chat = Chat(**payload)
        kwargs: dict[str, Any] = {
            "credentials": self.credentials,
        }
        
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
