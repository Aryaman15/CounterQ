# CounterQ

CounterQ is in Stage 0: repository foundation for the future Core Interaction Spike. This scaffold intentionally contains no Stage 1 interview behavior, no CounterQ domain tables, and no AI/provider integrations.

## Prerequisites

- Node.js 24+
- pnpm 11+
- Python 3.12+
- uv
- Docker Desktop

## Bootstrap

```sh
pnpm run bootstrap
```

## Local Infrastructure

```sh
pnpm run infra:up
pnpm run infra:down
```

PostgreSQL runs on `localhost:5432`; Redis runs on `localhost:6379`.

## Development

```sh
pnpm run dev:web
pnpm run dev:api
pnpm run dev:worker
```

The frontend uses Next.js at `http://localhost:3000`. The API uses FastAPI at `http://127.0.0.1:8000`; `GET /health` is the basic liveness endpoint.

## Tests

```sh
pnpm run test
pnpm run test:frontend
pnpm run test:backend
```

Backend integration tests expect the local PostgreSQL and Redis services to be running.

## Lint And Typecheck

```sh
pnpm run lint
pnpm run typecheck
```

## Migrations

```sh
pnpm run migrate
pnpm run migrate:create -- "message"
```

The Stage 0 Alembic baseline is intentionally empty apart from Alembic bookkeeping. Do not add CounterQ domain tables until the relevant vertical slice needs them.

## Contracts

Backend Pydantic/FastAPI OpenAPI is authoritative. Generate the TypeScript contract artifacts with:

```sh
pnpm run contracts
```

This writes `packages/contracts/schemas/openapi.json` and `packages/contracts/generated/openapi.ts`.

