#!/bin/bash
# Runs once, when the database volume is first created. The container's own
# superuser is kerp_owner (POSTGRES_USER). This creates the restricted runtime
# role that api and worker connect as; migrations grant it what it may touch.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE kerp_app LOGIN PASSWORD '${KERP_APP_PASSWORD}';
    REVOKE CREATE ON SCHEMA public FROM PUBLIC;
EOSQL
