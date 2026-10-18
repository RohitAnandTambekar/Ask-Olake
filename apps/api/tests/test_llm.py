"""Provider selection and the OpenRouter client (no network)."""

from types import SimpleNamespace

import pytest

from olake_api.config import Settings
from olake_api.llm import AnthropicLLM, LLMError, OpenAILLM, OpenRouterLLM, make_llm


def _settings(**overrides) -> Settings:
    # explicit values, so the test database fixture's environment (extractive, hashing) can't leak in
    base = {
        "llm_provider": "auto",
        "embedding_provider": "openai",
        "openai_api_key": None,
        "anthropic_api_key": None,
        "openrouter_api_key": None,
    }
    return Settings(_env_file=None, **{**base, **overrides})


def test_auto_prefers_openrouter_and_keeps_openai_for_embeddings():
    s = _settings(
        openrouter_api_key="sk-or-test", openai_api_key="sk-openai", anthropic_api_key="x"
    )
    assert s.resolved_llm == "openrouter"
    assert s.embedding_provider == "openai"
    assert isinstance(make_llm(s), OpenRouterLLM)


def test_auto_falls_back_without_openrouter_key():
    assert isinstance(make_llm(_settings(anthropic_api_key="x")), AnthropicLLM)
    llm = make_llm(_settings(openai_api_key="sk-openai"))
    assert type(llm) is OpenAILLM
    assert make_llm(_settings()) is None


def test_openrouter_client_points_at_openrouter():
    s = _settings(
        openrouter_api_key="sk-or-test",
        openrouter_model="anthropic/claude-sonnet-4",
        openrouter_site_url="https://olake.io",
    )
    llm = OpenRouterLLM(s)
    assert llm.name == "openrouter:anthropic/claude-sonnet-4"
    assert str(llm._client.base_url).startswith("https://openrouter.ai/api/v1")
    assert llm._client.api_key == "sk-or-test"
    assert llm._client.default_headers["X-Title"] == "Ask OLake"
    assert llm._client.default_headers["HTTP-Referer"] == "https://olake.io"


def test_openrouter_requires_its_key():
    with pytest.raises(LLMError, match="OPENROUTER_API_KEY"):
        OpenRouterLLM(_settings(llm_provider="openrouter"))


def test_openrouter_streams_with_max_tokens():
    llm = OpenRouterLLM(_settings(openrouter_api_key="sk-or-test"))
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        for piece in ("Use CDC ", "[1]."):
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=piece))])

    llm._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    assert "".join(llm.stream([{"role": "user", "content": "q"}])) == "Use CDC [1]."
    assert calls[0]["max_tokens"] == llm.max_tokens
    assert "max_completion_tokens" not in calls[0]
    assert calls[0]["messages"][0]["role"] == "system"


def test_openrouter_sends_models_in_priority_order_as_fallbacks():
    llm = OpenRouterLLM(_settings(openrouter_api_key="sk-or-test"))
    assert llm.model == "z-ai/glm-4.7-flash"
    assert llm.fallbacks == ["google/gemini-2.5-flash-lite", "deepseek/deepseek-v4.1-flash"]
    assert llm._extra == {
        "extra_body": {
            "models": [
                "z-ai/glm-4.7-flash",
                "google/gemini-2.5-flash-lite",
                "deepseek/deepseek-v4.1-flash",
            ]
        }
    }


def test_fallback_list_drops_duplicates_and_blanks():
    s = _settings(
        openrouter_model="google/gemini-2.5-flash-lite",
        openrouter_fallback_models=" google/gemini-2.5-flash-lite, ,z-ai/glm-4.7-flash",
    )
    assert s.openrouter_models == ["google/gemini-2.5-flash-lite", "z-ai/glm-4.7-flash"]


def test_openai_requests_carry_no_openrouter_fields():
    assert OpenAILLM(_settings(openai_api_key="sk-openai"))._extra == {}
