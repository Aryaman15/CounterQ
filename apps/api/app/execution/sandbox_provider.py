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
        if data["status"] not in {
            "SUCCEEDED",
            "COMPILE_ERROR",
            "RUNTIME_ERROR",
            "TIMED_OUT",
            "OUTPUT_LIMIT_EXCEEDED",
            "PROVIDER_ERROR",
        }:
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
        if data["status"] == "SUCCEEDED" and set(raw_by_identifier) != set(definitions):
            raise ExecutorProviderError()
        if data["status"] != "SUCCEEDED" and raw_by_identifier:
            raise ExecutorProviderError()
        cases: list[ExecutionCaseOutcome] = []
        if data["status"] == "SUCCEEDED":
            for definition in request.cases:
                raw_case = raw_by_identifier[definition.identifier]
                actual_output_value = raw_case.get("actual_output")
                if actual_output_value is not None and not isinstance(actual_output_value, str):
                    raise ExecutorProviderError()
                if (
                    isinstance(actual_output_value, str)
                    and len(actual_output_value.encode("utf-8")) > request.output_limit_bytes
                ):
                    raise ExecutorProviderError()
                duration_value = raw_case.get("duration_ms")
                if duration_value is not None and (
                    not isinstance(duration_value, int)
                    or isinstance(duration_value, bool)
                    or duration_value < 0
                ):
                    raise ExecutorProviderError()
                actual_output = cast(str | None, actual_output_value)
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
                        duration_ms=cast(int | None, duration_value),
                        failure_classification=compared.failure_classification,
                    )
                )
        stdout = _bounded_text(data.get("stdout", ""), request.output_limit_bytes)
        stderr = _bounded_text(data.get("stderr", ""), request.output_limit_bytes)
        compiler_output = _bounded_text(data.get("compiler_output", ""), request.output_limit_bytes)
        return ExecutionOutcome(
            status=data["status"],
            provider_run_id=cast(str | None, data.get("provider_run_id")),
            runtime_version=cast(str | None, data.get("runtime_version")),
            stdout=stdout,
            stderr=stderr,
            compiler_output=compiler_output,
            exit_code=cast(int | None, data.get("exit_code")),
            timed_out=bool(data.get("timed_out", False)),
            output_truncated=bool(data.get("output_truncated", False)),
            duration_ms=cast(int | None, data.get("duration_ms")),
            memory_bytes=cast(int | None, data.get("memory_bytes")),
            cases=tuple(cases),
        )


def _bounded_text(value: object, byte_limit: int) -> str:
    if not isinstance(value, str) or len(value.encode("utf-8")) > byte_limit:
        raise ExecutorProviderError()
    return value
