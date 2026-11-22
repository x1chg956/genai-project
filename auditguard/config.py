"""Typed settings loaded from configs/base.yaml plus an optional experiment override.

`${VAR}` and `${VAR:-default}` in string values are expanded from the process environment,
falling back to `.env`. Expansion happens after YAML parsing, so an environment value can
never inject YAML structure. Secrets are `SecretStr` and never appear in repr, logs or the
config hash.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, SecretStr

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "configs" / "base.yaml"
DEFAULT_ENV_FILE = REPO_ROOT / ".env"

StructuredMethod = Literal["json_schema", "function_calling", "json_mode"]

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(ValueError):
    """Raised when the config is missing a value the caller needs."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _empty_to_none(value: Any) -> Any:
    """An unset ${VAR} expands to "", which means "not configured" for optional fields."""
    return None if value == "" else value


_Blank = BeforeValidator(_empty_to_none)
OptSecret = Annotated[SecretStr | None, _Blank]
OptStr = Annotated[str | None, _Blank]
OptMethod = Annotated[StructuredMethod | None, _Blank]


class CostRates(_Strict):
    input: Decimal
    output: Decimal


class ProviderSettings(_Strict):
    base_url: str
    api_key: OptSecret = None
    timeout_s: float = 60
    extra_body: dict[str, Any] | None = None
    structured_method: OptMethod = None
    cost_per_1k_tokens: CostRates | None = None


class ModelSettings(_Strict):
    provider: str
    id: OptStr
    temperature: float = 0
    max_tokens: int | None = None
    stop: list[str] | None = None
    structured_method: OptMethod = None
    mode: Literal["chat", "completions_raw"] = "chat"
    prompt_template: OptStr = None
    enabled: bool = True


class CaseBudget(_Strict):
    steps: int
    tool_calls: int
    tokens: int
    seconds: int
    cost_usd: Decimal


class Budgets(_Strict):
    case: CaseBudget
    critic_revisions: int


class RetrievalSettings(_Strict):
    bm25: bool
    vector: bool
    rerank: bool
    decompose: bool
    k_bm25: int
    k_vector: int
    k_final: int
    rrf_k: int
    score_floor: float


class RulesSettings(_Strict):
    pack: str
    version: str


class CorpusSettings(_Strict):
    manifest: str
    version: str | None = None
    passages: str


class DatabaseSettings(_Strict):
    host: str
    port: int
    name: str
    user: str
    password: OptSecret = None


class Databases(_Strict):
    app: DatabaseSettings
    groundtruth: DatabaseSettings
    ledger_ro: DatabaseSettings


class QdrantSettings(_Strict):
    url: str


class InputGuardrails(_Strict):
    schema_check: bool = Field(alias="schema")
    pseudonymise: bool
    prompt_guard: bool
    llama_guard: bool
    scope: bool


class OutputGuardrails(_Strict):
    provenance: bool
    pii: bool
    neutral_wording: bool
    tipping_off: bool


class Guardrails(_Strict):
    input: InputGuardrails
    output: OutputGuardrails


class TracingSettings(_Strict):
    backend: Literal["langsmith", "langfuse", "none"]
    project: str
    endpoint: str
    api_key: OptSecret = None
    workspace_id: OptStr = None


class IngestSettings(_Strict):
    window_days: int = Field(ge=1, le=366)


class CheckpointerSettings(_Strict):
    backend: Literal["postgres", "sqlite"]
    schema_: str = Field(alias="schema", pattern=r"^[a-z_][a-z0-9_]*$")
    path: str


class NamedRef(_Strict):
    name: str
    code: str


class OfficerRef(_Strict):
    id: str
    designation: str


class StrReportSettings(_Strict):
    reason_codes: str
    reporting_entity: NamedRef
    principal_officer: OfficerRef
    branch: NamedRef


class ShortTermMemory(_Strict):
    keep_turns: int


class LongTermMemory(_Strict):
    enabled: bool
    max_lessons: int


class MemorySettings(_Strict):
    short_term: ShortTermMemory
    long_term: LongTermMemory


class RetrySettings(_Strict):
    max_attempts: int = Field(ge=1, le=3)
    base_delay_s: float = Field(ge=0)


class AuditSettings(_Strict):
    backend: Literal["postgres", "memory", "none"]
    schema_: str = Field(alias="schema", pattern=r"^[a-z_][a-z0-9_]*$")
    retry: RetrySettings


class ApprovalSettings(_Strict):
    ttl_minutes: int
    hmac_key: OptSecret = None
    backend: Literal["postgres", "sqlite"]
    schema_: str = Field(alias="schema", pattern=r"^[a-z_][a-z0-9_]*$")
    store_path: str


class FormLimits(_Strict):
    max_payload_bytes: int
    max_grounds_chars: int


class FilingPortalSettings(_Strict):
    db_path: str
    form_limits: FormLimits


class McpServerSettings(_Strict):
    module: str
    port: int


class McpSettings(_Strict):
    transport: Literal["stdio", "http"]
    http_host: str
    servers: dict[str, McpServerSettings] = Field(default_factory=dict)
    token_key: OptSecret = None
    token_ttl_minutes: int
    max_rows: int = Field(le=200)
    allow_list: dict[str, list[str]]
    description_hashes: dict[str, str] = Field(default_factory=dict)


class Settings(_Strict):
    config_version: int
    profile: str
    timezone: str
    providers: dict[str, ProviderSettings]
    models: dict[str, ModelSettings]
    prompts: dict[str, str]
    graph: Literal["c0", "c1", "c2"]
    tracing: TracingSettings
    budgets: Budgets
    retrieval: RetrievalSettings
    rules: RulesSettings
    corpus: CorpusSettings
    databases: Databases
    qdrant: QdrantSettings
    guardrails: Guardrails
    memory: MemorySettings
    approval: ApprovalSettings
    ingest: IngestSettings
    checkpointer: CheckpointerSettings
    audit: AuditSettings
    str_report: StrReportSettings
    filing_portal: FilingPortalSettings
    mcp: McpSettings

    @property
    def config_hash(self) -> str:
        """SHA-256 of the resolved settings. Secrets are masked, so key rotation keeps the hash."""
        dumped = self.model_dump(mode="json", by_alias=True)
        canonical = json.dumps(dumped, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def model_for(self, role: str) -> ModelSettings:
        try:
            return self.models[role]
        except KeyError:
            raise ConfigError(
                f"unknown model role {role!r}; known: {sorted(self.models)}"
            ) from None

    def provider_for(self, role: str) -> ProviderSettings:
        model = self.model_for(role)
        try:
            return self.providers[model.provider]
        except KeyError:
            raise ConfigError(
                f"models.{role}.provider is {model.provider!r}, which is not in providers "
                f"({sorted(self.providers)})"
            ) from None

    def structured_method_for(self, role: str) -> StructuredMethod:
        """Per-role method, else the provider's, else json_schema (docs/13 §4)."""
        return (
            self.model_for(role).structured_method
            or self.provider_for(role).structured_method
            or "json_schema"
        )


def resolve_path(path: str) -> Path:
    """Config paths are relative to the repo root unless absolute."""
    p = Path(path)
    return p if p.is_absolute() else REPO_ROOT / p


def require_secret(value: SecretStr | None, name: str) -> bytes:
    """The secret's bytes, or a ConfigError naming the variable (never its value)."""
    if value is None or not value.get_secret_value():
        raise ConfigError(f"{name} is not set; add it to .env")
    return value.get_secret_value().encode("utf-8")


def expand_vars(value: Any, environ: Mapping[str, str]) -> Any:
    """Recursively expand ${VAR} and ${VAR:-default} in every string of a parsed YAML tree."""
    if isinstance(value, str):
        return _VAR.sub(lambda m: environ.get(m.group(1)) or (m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: expand_vars(v, environ) for k, v in value.items()}
    if isinstance(value, list):
        return [expand_vars(v, environ) for v in value]
    return value


def deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Return base with override merged in; nested mappings merge, everything else replaces."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


def build_environ(env_file: Path | None) -> dict[str, str]:
    """Process environment over .env values; .env never overrides a variable already set."""
    file_values: dict[str, str] = {}
    if env_file is not None and env_file.is_file():
        file_values = {k: v for k, v in dotenv_values(env_file).items() if v is not None}
    return {**file_values, **os.environ}


def load_settings(
    config_path: Path | str = DEFAULT_CONFIG,
    experiment: Path | str | None = None,
    *,
    env_file: Path | None = DEFAULT_ENV_FILE,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Load base config, merge an optional experiment override, expand variables, validate.

    Pass `environ` to use an explicit environment (tests); otherwise the process
    environment is used, with `env_file` filling in variables that are not set.
    """
    raw = _read_yaml(Path(config_path))
    if experiment is not None:
        raw = deep_merge(raw, _read_yaml(Path(experiment)))
    env = dict(environ) if environ is not None else build_environ(env_file)
    return Settings.model_validate(expand_vars(raw, env))
