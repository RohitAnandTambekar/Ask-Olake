"""LLM providers behind one streaming interface.

- openrouter: OpenRouter's OpenAI-compatible API (any model it hosts) via the OpenAI SDK
- anthropic:  Claude via the official SDK
- openai:     OpenAI chat completions via the official SDK
- extractive: no model at all; used when no key is configured or the provider is down,
              so the assistant degrades to "here are the most relevant passages".
"""

from collections.abc import Iterator
from typing import Protocol

from olake_api.config import Settings
from olake_api.prompts import SYSTEM_PROMPT


class LLMError(RuntimeError):
    pass


class LLM(Protocol):
    name: str

    def stream(self, messages: list[dict]) -> Iterator[str]: ...


class AnthropicLLM:
    def __init__(self, settings: Settings):
        import anthropic

        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set")
        self._client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
        )
        self._errors = (anthropic.APIError,)
        self.model = settings.anthropic_model
        self.max_tokens = settings.llm_max_output_tokens
        self.name = f"anthropic:{self.model}"

    def stream(self, messages: list[dict]) -> Iterator[str]:
        try:
            with self._client.messages.stream(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=messages,
                temperature=0,
            ) as stream:
                yield from stream.text_stream
        except self._errors as exc:
            raise LLMError(f"Anthropic API error: {exc}") from exc


class OpenAILLM:
    provider = "openai"
    label = "OpenAI"
    max_tokens_param = "max_completion_tokens"

    def _options(self, settings: Settings) -> tuple[str | None, str, dict]:
        """(api key, model, extra client options) for this provider."""
        return settings.openai_api_key, settings.openai_chat_model, {}

    def _request_extra(self, settings: Settings) -> dict:
        """Extra fields for every chat request; none for OpenAI itself."""
        return {}

    def __init__(self, settings: Settings):
        import openai

        api_key, model, options = self._options(settings)
        self._extra = self._request_extra(settings)
        if not api_key:
            raise LLMError(f"{self.provider.upper()}_API_KEY is not set")
        self._client = openai.OpenAI(
            api_key=api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=2,
            **options,
        )
        self._errors = (openai.OpenAIError,)
        self.model = model
        self.max_tokens = settings.llm_max_output_tokens
        self.name = f"{self.provider}:{self.model}"

    def stream(self, messages: list[dict]) -> Iterator[str]:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": SYSTEM_PROMPT}, *messages],
                stream=True,
                **{self.max_tokens_param: self.max_tokens},
                **self._extra,
            )
            for event in response:
                if event.choices and event.choices[0].delta and event.choices[0].delta.content:
                    yield event.choices[0].delta.content
        except self._errors as exc:
            raise LLMError(f"{self.label} API error: {exc}") from exc


class OpenRouterLLM(OpenAILLM):
    """OpenRouter's OpenAI-compatible chat API, in front of many model providers."""

    provider = "openrouter"
    label = "OpenRouter"
    max_tokens_param = "max_tokens"  # the parameter OpenRouter documents

    def _options(self, settings: Settings) -> tuple[str | None, str, dict]:
        headers = {"X-Title": "Ask OLake"}
        if settings.openrouter_site_url:
            headers["HTTP-Referer"] = settings.openrouter_site_url
        return (
            settings.openrouter_api_key,
            settings.openrouter_model,
            {"base_url": settings.openrouter_base_url, "default_headers": headers},
        )

    def _request_extra(self, settings: Settings) -> dict:
        # OpenRouter tries these in order when one is down, rate-limited or refuses
        self.fallbacks = settings.openrouter_models[1:]
        return {"extra_body": {"models": settings.openrouter_models}}


def make_llm(settings: Settings) -> LLM | None:
    """None means extractive mode."""
    provider = settings.resolved_llm
    if provider == "openrouter":
        return OpenRouterLLM(settings)
    if provider == "anthropic":
        return AnthropicLLM(settings)
    if provider == "openai":
        return OpenAILLM(settings)
    return None
