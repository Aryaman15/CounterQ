$ErrorActionPreference = "Stop"
$env:COUNTERQ_SANDBOX_EVALUATION = "1"
uv run --directory apps/api pytest tests/evaluation/stage3b/test_trusted_harness_sandbox.py -q
