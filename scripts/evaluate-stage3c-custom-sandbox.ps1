$ErrorActionPreference = "Stop"
$env:COUNTERQ_SANDBOX_EVALUATION = "1"
uv run --directory apps/api pytest tests/evaluation/stage3c/custom_sandbox -q
