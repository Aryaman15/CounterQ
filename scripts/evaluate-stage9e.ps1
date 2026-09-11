$ErrorActionPreference = "Stop"
$env:COUNTERQ_SANDBOX_EVALUATION = "1"

uv run --directory apps/api pytest tests/evaluation/stage9e tests/test_ai_gateway.py tests/test_migrations.py -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

pnpm --filter @counterq/web exec vitest run tests/stage9e-custom-problem.test.tsx tests/stage9b-self-serve.test.tsx
exit $LASTEXITCODE
