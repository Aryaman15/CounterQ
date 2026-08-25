"""Provider-neutral boundary for isolated code execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class ExecutorProviderError(RuntimeError):
    safe_message = "Code execution is temporarily unavailable."


@dataclass(frozen=True)
class ExecutionCase:
    identifier: str
    input_json: dict[str, object]
    expected_output: str
    visible: bool = True


@dataclass(frozen=True)
class ExecutionRequest:
    language: str
    source_code: str
    harness: str
    cases: tuple[ExecutionCase, ...]
    compile_timeout_seconds: int
    run_timeout_seconds: int
    memory_limit_mb: int
    output_limit_bytes: int


@dataclass(frozen=True)
class ExecutionCaseOutcome:
    identifier: str
    actual_output: str | None
    status: str
    duration_ms: int | None = None
    failure_classification: str | None = None


@dataclass(frozen=True)
class ExecutionOutcome:
    status: str
    provider_run_id: str | None
    stdout: str = ""
    stderr: str = ""
    compiler_output: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    output_truncated: bool = False
    duration_ms: int | None = None
    memory_bytes: int | None = None
    cases: tuple[ExecutionCaseOutcome, ...] = ()


class ExecutorProvider(Protocol):
    provider_name: str

    async def execute(self, request: ExecutionRequest) -> ExecutionOutcome: ...


class FakeExecutorProvider:
    provider_name = "fake"

    def __init__(self, outcome: ExecutionOutcome) -> None:
        self.outcome = outcome
        self.requests: list[ExecutionRequest] = []

    async def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        self.requests.append(request)
        return self.outcome
