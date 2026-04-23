import json
from typing import Any

from litellm import completion

from app.core.config import get_settings


class LLMRouter:
    def __init__(self) -> None:
        self.settings = get_settings()

    def chat(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> str:
        selected_model = model or self.settings.llm_model

        kwargs: dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
            "temperature": self.settings.llm_temperature if temperature is None else temperature,
            "max_tokens": self.settings.llm_max_tokens if max_tokens is None else max_tokens,
            "timeout": self.settings.llm_timeout_seconds,
        }

        if self.settings.llm_api_base:
            kwargs["api_base"] = self.settings.llm_api_base
        if self.settings.llm_api_key:
            kwargs["api_key"] = self.settings.llm_api_key

        resp = completion(**kwargs)
        return resp.choices[0].message.content or ""

    def chat_json(
        self,
        messages: list[dict[str, str]],
        model: str | None = None,
        temperature: float = 0,
        max_tokens: int = 800,
    ) -> dict[str, Any]:
        raw = self.chat(messages=messages, model=model, temperature=temperature, max_tokens=max_tokens)
        return self._safe_parse_json(raw)

    @staticmethod
    def _safe_parse_json(raw: str) -> dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`")
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        return {}


llm_router = LLMRouter()
