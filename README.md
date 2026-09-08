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

## Local Configuration

CounterQ uses separate environment files for the server processes and the Next.js application. Keep real values only in the ignored local files; never commit them.

### FastAPI, worker, and infrastructure

Repository-root `.env` configures FastAPI, the background worker, local infrastructure integrations, the CounterQ auth verifier, and OpenAI providers:

```sh
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env` for live realtime testing. The key is server-only: do not put it in `NEXT_PUBLIC_*`, frontend code, generated contracts, or committed files. The API settings loader continues to resolve this file from the repository root even when the API command runs from `apps/api`.

Useful realtime defaults are documented in `.env.example`:

```sh
COUNTERQ_REALTIME_PROVIDER=openai
COUNTERQ_REALTIME_MODEL=gpt-realtime-2.1
COUNTERQ_REALTIME_VOICE=marin
COUNTERQ_REALTIME_TRANSCRIPTION_MODEL=gpt-live-transcribe
```

### Next.js and Clerk

The Next.js project is rooted at `apps/web`, so its local environment file must live there. Create it with:

```sh
cp apps/web/.env.example apps/web/.env.local
```

`apps/web/.env.local` configures the browser-visible API base URL plus the publishable key and server-only secret used by `@clerk/nextjs`:

```text
NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=...
CLERK_SECRET_KEY=...
```

Only variables prefixed with `NEXT_PUBLIC_` are exposed to browser code. `CLERK_SECRET_KEY` must remain private even though it belongs in the Next.js environment file.

#### Real Clerk development setup

1. Create or select a **Development** application in the [Clerk Dashboard](https://dashboard.clerk.com/).
2. Open **API keys**. In **Quick Copy**, copy the development **Publishable Key** and **Secret Key** into `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` in `apps/web/.env.local`.
3. On the same **API keys** page, choose **Show JWT public key**, then copy the **PEM Public Key**. Put the complete PEM, including its begin/end lines, in repository-root `.env` as the quoted multiline value of `COUNTERQ_CLERK_JWT_VERIFICATION_KEY`.
4. Open **Domains** in the Clerk Dashboard and copy the instance's **Frontend API URL** (for example, a development URL ending in `clerk.accounts.dev`). This URL is the Clerk session token issuer; put it in `COUNTERQ_CLERK_ISSUER` without a `/.well-known/jwks.json` suffix.
5. Keep `http://localhost:3000` as the standard local web origin in `COUNTERQ_ALLOWED_FRONTEND_ORIGINS`. The example also permits the exact compatibility origin `http://127.0.0.1:3000`; CounterQ continues to validate the token's authorized-party claim against exact configured origins.

The resulting repository-root `.env` authentication entries are:

```text
COUNTERQ_AUTH_PROVIDER=clerk
COUNTERQ_CLERK_ISSUER=https://your-development-instance.clerk.accounts.dev
COUNTERQ_CLERK_JWT_VERIFICATION_KEY="-----BEGIN PUBLIC KEY-----
...
-----END PUBLIC KEY-----"
COUNTERQ_ALLOWED_FRONTEND_ORIGINS=http://localhost:3000,http://127.0.0.1:3000
```

CounterQ's FastAPI verifier uses this PEM directly for networkless RS256 signature verification; it does not use the Clerk secret key or fetch a JWKS during a request. Clerk documents the [Next.js key setup](https://clerk.com/docs/nextjs/getting-started/quickstart) and [manual JWT verification](https://clerk.com/docs/guides/sessions/manual-jwt-verification) separately.

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

The standard `pnpm run dev:web` command serves Next.js at `http://localhost:3000`, which is the canonical browser origin for local Clerk development. The API remains at `http://127.0.0.1:8000`; `GET /health` is the basic liveness endpoint.

Open the Stage 1 Interview Room preview at:

```text
http://localhost:3000/interview/demo
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
