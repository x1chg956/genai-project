#!/bin/sh
# First-start init for the Compose Postgres (same layout as scripts/db_bootstrap.sh):
# an app role and database, and a separate groundtruth role and database. Each database
# accepts connections from its own role only. Runs once, when the data volume is empty.
set -eu

psql -v ON_ERROR_STOP=1 -U postgres -d postgres -q \
  -v app_user="$APP_USER" -v app_db="$APP_DB" -v app_pw="$APP_PASSWORD" \
  -v gt_user="$GT_USER" -v gt_db="$GT_DB" -v gt_pw="$GT_PASSWORD" <<'SQL'
CREATE ROLE :"app_user" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_pw';
CREATE ROLE :"gt_user"  LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'gt_pw';
CREATE DATABASE :"app_db" OWNER :"app_user";
CREATE DATABASE :"gt_db"  OWNER :"gt_user";
REVOKE ALL ON DATABASE :"app_db" FROM PUBLIC;
REVOKE ALL ON DATABASE :"gt_db"  FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"app_db" TO :"app_user";
GRANT CONNECT, TEMPORARY ON DATABASE :"gt_db"  TO :"gt_user";
SQL
echo "auditguard init: created databases $APP_DB and $GT_DB"
