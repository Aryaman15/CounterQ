"""HTTP adapter for the deliberately isolated local C++ sandbox service."""

from __future__ import annotations

from typing import cast

import httpx

from app.execution.codecs import ExecutionCodecError, compare_output, validate_output
from app.execution.provider import (
    ExecutionCaseOutcome,
    ExecutionOutcome,
    ExecutionRequest,
    ExecutorProviderError,
)


class LocalSandboxExecutorProvider:
    provider_name = "local_cpp_sandbox"

    def __init__(self, base_url: str, *, timeout_seconds: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        payload = {
            "language": request.language,
            "source_code": request.source_code,
            "harness": request.harness,
            "cases": [
                {
                    "identifier": case.identifier,
                    "input_json": case.input_json,
                    "expected_output": case.expected_output,
                    "visible": case.visible,
                }
                for case in request.cases
            ],
            "compile_timeout_seconds": request.compile_timeout_seconds,
            "run_timeout_seconds": request.run_timeout_seconds,
            "memory_limit_mb": request.memory_limit_mb,
            "output_limit_bytes": request.output_limit_bytes,
        }
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(f"{self._base_url}/execute", json=payload)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ExecutorProviderError() from exc
        if not isinstance(data, dict) or not isinstance(data.get("status"), str):
            raise ExecutorProviderError()
        raw_cases = data.get("cases", [])
        if not isinstance(raw_cases, list):
            raise ExecutorProviderError()
        definitions = {case.identifier: case for case in request.cases}
        raw_by_identifier: dict[str, dict[str, object]] = {}
        for raw_case in raw_cases:
            if not isinstance(raw_case, dict) or not isinstance(raw_case.get("identifier"), str):
                raise ExecutorProviderError()
            identifier = raw_case["identifier"]
            if identifier not in definitions or identifier in raw_by_identifier:
                raise ExecutorProviderError()
            raw_by_identifier[identifier] = raw_case
        cases: list[ExecutionCaseOutcome] = []
        if data["status"] == "SUCCEEDED":
            for definition in request.cases:
                raw_case = raw_by_identifier.get(definition.identifier, {})
                actual_output = cast(str | None, raw_case.get("actual_output"))
                try:
                    compared = (
                        validate_output(actual_output, definition.return_type)
                        if definition.expected_output is None
                        else compare_output(
                            actual_output,
                            definition.expected_output,
                            definition.return_type,
                            definition.comparator,
                        )
                    )
                except ExecutionCodecError as exc:
                    raise ExecutorProviderError() from exc
                cases.append(
                    ExecutionCaseOutcome(
                        identifier=definition.identifier,
                        actual_output=compared.actual_output,
                        status=compared.status,
                        duration_ms=cast(int | None, raw_case.get("duration_ms")),
                        failure_classification=compared.failure_classification,
                    )
                )
        return ExecutionOutcome(
            status=data["status"],
            provider_run_id=cast(str | None, data.get("provider_run_id")),
            runtime_version=cast(str | None, data.get("runtime_version")),
            stdout=cast(str, data.get("stdout", "")),
            stderr=cast(str, data.get("stderr", "")),
            compiler_output=cast(str, data.get("compiler_output", "")),
            exit_code=cast(int | None, data.get("exit_code")),
            timed_out=bool(data.get("timed_out", False)),
            output_truncated=bool(data.get("output_truncated", False)),
            duration_ms=cast(int | None, data.get("duration_ms")),
            memory_bytes=cast(int | None, data.get("memory_bytes")),
            cases=tuple(cases),
        )
