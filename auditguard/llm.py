"""Model factory: role -> provider -> client. The only module that creates model clients.

Code asks for a role (`supervisor`, `specialist`, `decomposer`, `judge`, `guard`); which
server and model serve it is decided in config (docs/13). No base URL or model ID lives here.
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.language_models import BaseChatModel, BaseLLM
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI, OpenAI
from pydantic import BaseModel, SecretStr

from auditguard.config import ConfigError, ModelSettings, ProviderSettings, Settings, load_settings

MAX_RETRIES = 2


def tracing_callbacks(settings: Settings) -> list[BaseCallbackHandler]:
    """Tracing callbacks (Langfuse or LangSmith) for every model call. None configured yet."""
    del settings
    return []


def _resolve(role: str, settings: Settings) -> tuple[ModelSettings, ProviderSettings, SecretStr]:
    model = settings.model_for(role)
    provider = settings.provider_for(role)
    if not model.enabled:
        raise ConfigError(f"models.{role} is disabled")
    if not model.id:
        raise ConfigError(f"models.{role}.id is not set")
    if provider.api_key is None:
        raise ConfigError(
            f"providers.{model.provider}.api_key is empty (role {role!r}); set its key in .env"
        )
    return model, provider, provider.api_key


def get_model(role: str, settings: Settings | None = None) -> BaseChatModel:
    """Chat model for a role, built only from config. No network call is made here."""
    settings = settings or load_settings()
    model, provider, api_key = _resolve(role, settings)
    if model.mode != "chat":
        raise ConfigError(f"models.{role}.mode is {model.mode!r}; use get_guard() for this role")
    return ChatOpenAI(
        base_url=provider.base_url,
        api_key=api_key,
        model=model.id,
        temperature=model.temperature,
        max_tokens=model.max_tokens,
        stop=model.stop,
        timeout=provider.timeout_s,
        max_retries=MAX_RETRIES,
        extra_body=provider.extra_body or None,
        callbacks=tracing_callbacks(settings) or None,
    )


def get_structured_model(
    role: str, schema: type[BaseModel], settings: Settings | None = None
) -> Runnable[Any, Any]:
    """Chat model bound to a Pydantic schema with the role's structured-output method."""
    settings = settings or load_settings()
    method = settings.structured_method_for(role)
    strict = True if method == "json_schema" else None
    return get_model(role, settings).with_structured_output(schema, method=method, strict=strict)


def get_guard(settings: Settings | None = None, role: str = "guard") -> BaseChatModel | BaseLLM:
    """Guard client: a completions client in `completions_raw` mode, else a chat model.

    In `completions_raw` mode the caller renders the official Llama Guard prompt from
    `models.guard.prompt_template` (docs/13 §1b); the server's chat template is bypassed.
    """
    settings = settings or load_settings()
    model, provider, api_key = _resolve(role, settings)
    if model.mode == "chat":
        return get_model(role, settings)
    if not model.prompt_template:
        raise ConfigError(f"models.{role}.prompt_template is required in completions_raw mode")
    return OpenAI(
        base_url=provider.base_url,
        api_key=api_key,
        model=model.id,
        temperature=model.temperature,
        max_tokens=model.max_tokens or 20,
        timeout=provider.timeout_s,
        max_retries=MAX_RETRIES,
        callbacks=tracing_callbacks(settings) or None,
    )
