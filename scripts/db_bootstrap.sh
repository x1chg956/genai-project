#!/usr/bin/env bash
# Create the AuditGuard roles and databases on a local Postgres (idempotent).
#
#   auditguard  role + database  (ledger, cases, approvals, audit, checkpoints)
#   groundtruth role + database  (labels; only the eval harness and generator connect)
#   auditguard_ro role           (MCP transactions + aml_engine: CONNECT to auditguard only;
#                                 SELECT on ledger.* is granted by `make seed`)
#
# Each database accepts connections from its own role only, so the app role can
# never reach groundtruth. Passwords come from .env and are never printed.
#
# Usage: scripts/db_bootstrap.sh            (prompts for the admin password)
# Env:   PG_BIN (default /Library/PostgreSQL/17/bin), PGADMIN_USER (default postgres),
#        PGADMIN_PASSWORD (optional; otherwise psql prompts)
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ ! -f .env ]]; then
  echo "error: .env not found. Run: cp .env.example .env and fill in the passwords." >&2
  exit 1
fi
set -a
# shellcheck disable=SC1091
. ./.env
set +a

PG_BIN="${PG_BIN:-/Library/PostgreSQL/17/bin}"
PSQL="$PG_BIN/psql"
[[ -x "$PSQL" ]] || PSQL="$(command -v psql || true)"
[[ -n "$PSQL" && -x "$PSQL" ]] || { echo "error: psql not found; set PG_BIN" >&2; exit 1; }

HOST="${POSTGRES_HOST:-localhost}"
PORT="${POSTGRES_PORT:-5432}"
APP_USER="${POSTGRES_USER:-auditguard}"
APP_DB="${POSTGRES_DB:-auditguard}"
GT_USER="${GROUNDTRUTH_USER:-groundtruth}"
GT_DB="${GROUNDTRUTH_DB:-groundtruth}"
RO_USER="${LEDGER_RO_USER:-auditguard_ro}"
ADMIN="${PGADMIN_USER:-postgres}"

for var in POSTGRES_PASSWORD GROUNDTRUTH_PASSWORD LEDGER_RO_PASSWORD; do
  val="${!var:-}"
  if [[ -z "$val" ]]; then
    echo "error: $var is empty in .env" >&2; exit 1
  fi
  if [[ "$val" == *"'"* || "$val" == *'\'* ]]; then
    echo "error: $var must not contain ' or \\" >&2; exit 1
  fi
done
if [[ "$POSTGRES_PASSWORD" == "$GROUNDTRUTH_PASSWORD" ]]; then
  echo "error: POSTGRES_PASSWORD and GROUNDTRUTH_PASSWORD must differ" >&2; exit 1
fi
if [[ "$LEDGER_RO_PASSWORD" == "$POSTGRES_PASSWORD" || "$LEDGER_RO_PASSWORD" == "$GROUNDTRUTH_PASSWORD" ]]; then
  echo "error: LEDGER_RO_PASSWORD must differ from the other passwords" >&2; exit 1
fi
if [[ "$APP_USER" == "$GT_USER" ]]; then
  echo "error: POSTGRES_USER and GROUNDTRUTH_USER must differ" >&2; exit 1
fi

if [[ -n "${PGADMIN_PASSWORD:-}" ]]; then export PGPASSWORD="$PGADMIN_PASSWORD"; fi

echo "Bootstrapping roles and databases on $HOST:$PORT as $ADMIN ..."
# SQL is sent on stdin so passwords never appear in the process list.
"$PSQL" -h "$HOST" -p "$PORT" -U "$ADMIN" -d postgres -v ON_ERROR_STOP=1 -q <<SQL
\set app_user '$APP_USER'
\set app_db '$APP_DB'
\set app_pw '$POSTGRES_PASSWORD'
\set gt_user '$GT_USER'
\set gt_db '$GT_DB'
\set gt_pw '$GROUNDTRUTH_PASSWORD'
\set ro_user '$RO_USER'
\set ro_pw '$LEDGER_RO_PASSWORD'

SELECT format('CREATE ROLE %I LOGIN', :'app_user')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'app_user') \gexec
SELECT format('CREATE ROLE %I LOGIN', :'gt_user')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'gt_user') \gexec
ALTER ROLE :"app_user" WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_pw';
ALTER ROLE :"gt_user"  WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'gt_pw';
SELECT format('CREATE ROLE %I LOGIN', :'ro_user')
 WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'ro_user') \gexec
ALTER ROLE :"ro_user"  WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'ro_pw';
ALTER ROLE :"ro_user"  SET default_transaction_read_only = on;

SELECT format('CREATE DATABASE %I OWNER %I', :'app_db', :'app_user')
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'app_db') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'gt_db', :'gt_user')
 WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'gt_db') \gexec

REVOKE ALL ON DATABASE :"app_db" FROM PUBLIC;
REVOKE ALL ON DATABASE :"gt_db"  FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"app_db" TO :"app_user";
GRANT CONNECT, TEMPORARY ON DATABASE :"gt_db"  TO :"gt_user";
GRANT CONNECT ON DATABASE :"app_db" TO :"ro_user";
SQL
unset PGPASSWORD

# Verify: each role reaches its own database, and the app role cannot reach groundtruth.
check() {  # check <user> <password> <db> <expect: ok|denied>
  local out
  if out=$(PGPASSWORD="$2" "$PSQL" -h "$HOST" -p "$PORT" -U "$1" -d "$3" -w -Atc "select 1" 2>&1); then
    [[ "$4" == ok ]] && echo "  PASS  $1 -> $3 connects" || { echo "  FAIL  $1 -> $3 should be denied"; return 1; }
  else
    [[ "$4" == denied ]] && echo "  PASS  $1 -> $3 denied" || { echo "  FAIL  $1 -> $3: $out"; return 1; }
  fi
}
echo "Verifying ..."
check "$APP_USER" "$POSTGRES_PASSWORD"    "$APP_DB" ok
check "$GT_USER"  "$GROUNDTRUTH_PASSWORD" "$GT_DB"  ok
check "$APP_USER" "$POSTGRES_PASSWORD"    "$GT_DB"  denied
check "$GT_USER"  "$GROUNDTRUTH_PASSWORD" "$APP_DB" denied
check "$RO_USER"  "$LEDGER_RO_PASSWORD"   "$APP_DB" ok
check "$RO_USER"  "$LEDGER_RO_PASSWORD"   "$GT_DB"  denied
echo "Done."
