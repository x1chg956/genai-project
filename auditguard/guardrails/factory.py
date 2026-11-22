"""Guardrails built from settings: the pseudonymiser and the output policy."""

from __future__ import annotations

from auditguard.config import Settings, require_secret, resolve_path
from auditguard.db import conninfo
from auditguard.guardrails.output import OutputPolicy
from auditguard.guardrails.pseudonymise import MemoryVault, PostgresVault, Pseudonymiser, Vault


def pseudonymiser(settings: Settings) -> Pseudonymiser:
    g = settings.guardrails
    key = require_secret(g.pseudonym_key, "PSEUDONYM_KEY")
    vault: Vault
    if g.vault.backend == "postgres":
        vault = PostgresVault(
            conninfo(settings.databases.app, application_name="vault"), g.vault.schema_
        )
    else:
        vault = MemoryVault()
    return Pseudonymiser(key, vault, enabled=g.input.pseudonymise)


def output_policy(settings: Settings) -> OutputPolicy:
    return OutputPolicy.load(resolve_path(settings.guardrails.output_policy))
