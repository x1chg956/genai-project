"""CLI: python -m data.generator --seed 42 --profile tiny [--dry-run]. Used by `make seed`."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date

import psycopg

from auditguard.config import REPO_ROOT, load_settings
from auditguard.db import conninfo, describe
from data.generator.generate import GeneratorError, generate, spec_sha256
from data.generator.spec import load_spec
from data.generator.writer import write_labels, write_ledger

SPEC = "data/generator/typology_spec.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate the synthetic ledger.")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--profile", default="tiny")
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2026, 3, 31))
    parser.add_argument("--spec", default=SPEC)
    parser.add_argument("--dry-run", action="store_true", help="generate and summarise only")
    args = parser.parse_args(argv)

    spec_path = REPO_ROOT / args.spec
    try:
        ds = generate(
            load_spec(spec_path),
            seed=args.seed,
            profile=args.profile,
            end_date=args.end_date,
            spec_hash=spec_sha256(spec_path),
        )
    except GeneratorError as exc:
        print(f"generator error: {exc}")
        return 2

    directions = Counter(r.txn.direction for r in ds.transactions)
    print(
        f"profile={ds.profile} seed={ds.seed}: {len(ds.accounts)} accounts, "
        f"{len(ds.transactions)} transactions ({directions['credit']} credits, "
        f"{directions['debit']} debits), {len(ds.alerts)} alerts: "
        f"{', '.join(a.alert_id for a in ds.alerts)}"
    )
    if args.dry_run:
        return 0

    settings = load_settings()
    app, gt = settings.databases.app, settings.databases.groundtruth
    try:
        granted = write_ledger(
            ds,
            conninfo(app, application_name="generator"),
            ro_user=settings.databases.ledger_ro.user,
        )
        write_labels(ds, conninfo(gt, application_name="generator"))
    except psycopg.Error as exc:
        print(
            f"database error ({type(exc).__name__}); check .env and run make db-bootstrap. "
            f"Targets: {describe(app)}, {describe(gt)}"
        )
        return 1
    print(f"wrote ledger to {describe(app)} and labels to {describe(gt)}")
    if not granted:
        print(
            f"warning: role {settings.databases.ledger_ro.user!r} not found, so the MCP servers "
            "cannot read the ledger; run make db-bootstrap"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
