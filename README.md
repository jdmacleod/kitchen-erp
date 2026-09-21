# Kitchen ERP

A self-hosted household kitchen system: vendors, products, purchases, and price
history first; recipes, costing, shopping trips, and inventory later. One
deployment serves one household. The specification lives in `docs/spec/`.

This repository is public and the deployment holds personal data. Read
`SECURITY.md` before contributing: nothing from a real receipt, a real home, or
a real person is ever committed, and the hooks in `.pre-commit-config.yaml`
enforce that.

## Quickstart

```bash
cp .env.example .env            # then change the two passwords
docker compose up -d --build    # db, api, worker, web
docker compose exec api kerp migrate
docker compose exec api kerp create-admin
```

Open <http://localhost:8080> and log in. The API is also reachable directly at
<http://localhost:8000/api/docs>.

On macOS run Ollama natively on the host; the default `OLLAMA_BASE_URL` reaches
it through `host.docker.internal`. On a Linux host with a GPU, add
`--profile llm` to run it in Docker and set `OLLAMA_BASE_URL=http://ollama:11434`.
The system is fully usable for manual workflows with no model server running.

## Tests

```bash
docker compose exec api pytest          # backend, against a throwaway database
docker compose exec api pytest -m llm   # opt-in, needs a live model
```

The suite creates a fresh database on the Compose `db` service for each run and
drops it afterwards, so it never touches the household's data.

## Development without Docker for the API

```bash
docker compose up -d db
cd backend && uv sync
export DATABASE_URL=postgresql+asyncpg://kerp_app:<app-pw>@127.0.0.1:5433/kerp
export MIGRATION_DATABASE_URL=postgresql+asyncpg://kerp_owner:<owner-pw>@127.0.0.1:5433/kerp
uv run kerp migrate
uv run uvicorn app.main:app --reload
uv run pytest
```

## Contributing

`make setup` installs the git hooks. `make check-pii` runs the personal-data
scanner. `docs/licensing.md` records what may be borrowed from the reference
projects and which third-party data carries attribution obligations.
