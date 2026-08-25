# CounterQ

CounterQ is in Stage 3A development. The repository includes the Core Interaction Spike, the `/interview/demo` Interview Room, and a local C++ execution vertical slice that sends exact canonical code snapshots to an isolated local sandbox.

The current realtime path is a development spike: FastAPI mints short-lived OpenAI Realtime browser credentials, and the browser connects directly to OpenAI over WebRTC. It does not implement Examiner reasoning, adaptive probes, canonical realtime transcript persistence, reports, Evidence, CounterMap, or Mastery.

## Prerequisites

- Node.js 24+
- pnpm 11+
- Python 3.12+
- uv
- Docker Desktop
- OpenAI API key with Realtime access for live voice testing

## Local Secrets

Local monorepo secrets live in the repository-root `.env` file:

```sh
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env` for live realtime testing. The key is server-only: do not put it in `NEXT_PUBLIC_*`, frontend code, generated contracts, or committed files.

Useful realtime defaults are documented in `.env.example`:

```sh
COUNTERQ_REALTIME_PROVIDER=openai
COUNTERQ_REALTIME_MODEL=gpt-realtime-2.1
COUNTERQ_REALTIME_VOICE=marin
COUNTERQ_REALTIME_TRANSCRIPTION_MODEL=gpt-live-transcribe
```

## Bootstrap

```sh
pnpm run bootstrap
```

## Local Infrastructure

```sh
pnpm run infra:up
pnpm run infra:down
```

PostgreSQL runs on `localhost:5432`; Redis runs on `localhost:6379`. The isolated execution sandbox is available to the local API through `http://127.0.0.1:8010`; it is not an application container and must remain the only local process that compiles or runs candidate C++.

## Development

```sh
pnpm run dev:web
pnpm run dev:api
pnpm run dev:worker
```

The frontend uses Next.js at `http://127.0.0.1:3000`. The API uses FastAPI at `http://127.0.0.1:8000`; `GET /health` is the basic liveness endpoint.

Open the Stage 1 Interview Room preview at:

```text
http://127.0.0.1:3000/interview/demo
```

For live realtime voice, start web and API, open the demo route, then use **Enable microphone**. Real provider testing consumes OpenAI API credit.

The **Run** control is a development-only C++ vertical slice. It creates a canonical code snapshot and shows only bounded visible-case results. It does not execute code in FastAPI or expose hidden-test judgment.

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
