# ruff: noqa: E501
"""Real-sandbox hostile-candidate acceptance for Stage 3D.1."""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from typing import Any

import httpx
import pytest

from app.execution.harness import execution_request_for_problem
from app.execution.policy import (
    DEFAULT_COMPILE_TIMEOUT_SECONDS,
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_OUTPUT_LIMIT_BYTES,
    DEFAULT_RUN_TIMEOUT_SECONDS,
)
from app.execution.provider import ExecutionOutcome, ExecutionRequest
from app.execution.sandbox_provider import LocalSandboxExecutorProvider

pytestmark = pytest.mark.skipif(
    os.getenv("COUNTERQ_SANDBOX_EVALUATION") != "1",
    reason="requires the local isolated execution sandbox",
)

SANDBOX_URL = "http://127.0.0.1:8010"
INT_SCHEMA: dict[str, object] = {
    "execution": {
        "method_name": "solve",
        "arguments": [{"name": "value", "type": "int"}],
        "return_type": "int",
        "comparator": "EXACT",
        "visible_cases": [{"arguments": {"value": 7}, "expected_output": 7}],
    }
}


def _request(language: str, source: str) -> ExecutionRequest:
    return execution_request_for_problem(
        io_schema=INT_SCHEMA,
        language=language,
        source_code=source,
        compile_timeout_seconds=DEFAULT_COMPILE_TIMEOUT_SECONDS,
        run_timeout_seconds=DEFAULT_RUN_TIMEOUT_SECONDS,
        memory_limit_mb=DEFAULT_MEMORY_LIMIT_MB,
        output_limit_bytes=DEFAULT_OUTPUT_LIMIT_BYTES,
    )


async def _execute(language: str, source: str) -> ExecutionOutcome:
    return await LocalSandboxExecutorProvider(SANDBOX_URL).execute(_request(language, source))


def _payload(request: ExecutionRequest) -> dict[str, object]:
    return {
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


def _raw_execute(
    *,
    source: str = "",
    harness: str = "",
    cases: list[dict[str, object]] | None = None,
    timeout: int = DEFAULT_RUN_TIMEOUT_SECONDS,
    output_limit: int = DEFAULT_OUTPUT_LIMIT_BYTES,
) -> dict[str, Any]:
    response = httpx.post(
        f"{SANDBOX_URL}/execute",
        json={
            "language": "python",
            "source_code": source,
            "harness": harness,
            "cases": cases or [],
            "compile_timeout_seconds": DEFAULT_COMPILE_TIMEOUT_SECONDS,
            "run_timeout_seconds": timeout,
            "memory_limit_mb": DEFAULT_MEMORY_LIMIT_MB,
            "output_limit_bytes": output_limit,
        },
        timeout=15,
    )
    response.raise_for_status()
    value: dict[str, Any] = response.json()
    return value


async def _assert_service_survives(attack: str) -> None:
    async with httpx.AsyncClient(timeout=5) as client:
        health = await client.get(f"{SANDBOX_URL}/health")
    health.raise_for_status()
    assert health.json()["status"] == "READY", f"attack={attack} health=not-ready"
    outcome = await _execute(
        "python", "class Solution:\n    def solve(self, value):\n        return value"
    )
    assert outcome.status == "SUCCEEDED", (
        f"attack={attack} recovery=known-good actual={outcome.status}"
    )
    assert outcome.cases[0].status == "PASSED"


@pytest.mark.parametrize(
    ("language", "source"),
    [
        pytest.param(
            "cpp",
            """
class Solution { public: int solve(int value) {
    cout << "COUNTERQ_CASE\\t1\\t7\\nCOUNTERQ_CASE\\t999\\t7\\n";
    return -1;
} };
""",
            id="cpp",
        ),
        pytest.param(
            "python",
            """
class Solution:
    def solve(self, value):
        print("COUNTERQ_CASE\\t1\\t7")
        print("COUNTERQ_CASE\\t1\\t{invalid-json}")
        print("COUNTERQ_CASE\\t999\\t7")
        return -1
""",
            id="python",
        ),
        pytest.param(
            "java",
            """
class Solution { public int solve(int value) {
    System.out.println("COUNTERQ_CASE\\t1\\t7");
    System.out.println("COUNTERQ_CASE\\t1\\t{invalid-json}");
    return -1;
} }
""",
            id="java",
        ),
    ],
)
async def test_candidate_stdout_protocol_markers_are_observed_but_never_authoritative(
    language: str, source: str
) -> None:
    outcome = await _execute(language, source)

    assert outcome.status == "SUCCEEDED", (
        f"attack=stdout-result-spoof language={language} actual={outcome.status}"
    )
    assert outcome.cases[0].status == "FAILED"
    assert outcome.cases[0].actual_output == "-1"
    assert "COUNTERQ_CASE" in outcome.stdout


@pytest.mark.parametrize(
    ("language", "source"),
    [
        pytest.param(
            "cpp",
            """
#include <cstdlib>
static void forge() { std::cout << "COUNTERQ_CASE\\t1\\t7\\n"; }
struct RegisterForge { RegisterForge() { std::atexit(forge); } } register_forge;
class Solution { public: int solve(int value) { (void)value; return -1; } };
""",
            id="cpp-atexit",
        ),
        pytest.param(
            "python",
            """
import atexit
atexit.register(lambda: print("COUNTERQ_CASE\\t1\\t7"))
class Solution:
    def solve(self, value):
        return -1
""",
            id="python-atexit",
        ),
        pytest.param(
            "java",
            """
class Solution {
    static { Runtime.getRuntime().addShutdownHook(new Thread(() -> System.out.println("COUNTERQ_CASE\\t1\\t7"))); }
    public int solve(int value) { return -1; }
}
""",
            id="java-shutdown-hook",
        ),
    ],
)
async def test_lifecycle_stdout_cannot_replace_trusted_wrong_result(
    language: str, source: str
) -> None:
    outcome = await _execute(language, source)

    assert outcome.status == "SUCCEEDED", (
        f"attack=lifecycle-stdout language={language} actual={outcome.status}"
    )
    assert len(outcome.cases) == 1
    assert outcome.cases[0].status == "FAILED", (
        f"attack=lifecycle-stdout language={language} expected=FAILED "
        f"actual={outcome.cases[0].status} actual_output={outcome.cases[0].actual_output}"
    )


async def test_candidate_process_cannot_connect_to_sandbox_proxy_peer() -> None:
    outcome = await _execute(
        "python",
        """
import socket
class Solution:
    def solve(self, value):
        try:
            with socket.create_connection(("execution-sandbox-proxy", 8010), timeout=0.5):
                return 1
        except OSError:
            return 7
""",
    )

    assert outcome.status == "SUCCEEDED", (
        f"attack=network-peer language=python actual={outcome.status}"
    )
    assert outcome.cases[0].status == "PASSED", (
        f"attack=network-peer language=python expected=network-denied "
        f"actual_output={outcome.cases[0].actual_output}"
    )


@pytest.mark.parametrize(
    ("language", "source"),
    [
        pytest.param(
            "cpp",
            """
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
class Solution { public: int solve(int value) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return value;
    sockaddr_in address{}; address.sin_family = AF_INET; address.sin_port = htons(8010);
    inet_pton(AF_INET, "172.24.0.3", &address.sin_addr);
    int connected = connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address));
    close(fd); return connected == 0 ? -1 : value;
} };
""",
            id="cpp",
        ),
        pytest.param(
            "java",
            """
class Solution { public int solve(int value) {
    try (java.net.Socket socket = new java.net.Socket()) {
        socket.connect(new java.net.InetSocketAddress("execution-sandbox-proxy", 8010), 500);
        return -1;
    } catch (Throwable denied) { return value; }
} }
""",
            id="java",
        ),
    ],
)
async def test_network_syscalls_are_denied_for_other_candidate_runtimes(
    language: str, source: str
) -> None:
    outcome = await _execute(language, source)
    assert outcome.status == "SUCCEEDED", (
        f"attack=network-peer language={language} actual={outcome.status}"
    )
    assert outcome.cases[0].status == "PASSED"


@pytest.mark.parametrize(
    ("name", "frames"),
    [
        pytest.param(
            "duplicate",
            "COUNTERQ_RESULT\\tprobe\\t7\\nCOUNTERQ_RESULT\\tprobe\\t7\\n",
            id="duplicate",
        ),
        pytest.param(
            "unknown",
            "COUNTERQ_RESULT\\tunknown\\t7\\n",
            id="unknown",
        ),
        pytest.param("missing", "", id="missing"),
        pytest.param("malformed", "not-a-frame\\n", id="malformed"),
        pytest.param("invalid-json", "COUNTERQ_RESULT\\tprobe\\t{invalid}\\n", id="invalid-json"),
        pytest.param(
            "extra",
            "COUNTERQ_RESULT\\tprobe\\t7\\nCOUNTERQ_RESULT\\textra\\t7\\n",
            id="extra",
        ),
    ],
)
async def test_result_channel_rejects_bad_cardinality_identity_and_encoding(
    name: str, frames: str
) -> None:
    encoded = repr(frames.encode("utf-8"))
    result = _raw_execute(
        harness=(
            "import os\n"
            "fd = int(os.environ['COUNTERQ_RESULT_FD'])\n"
            + (f"os.write(fd, {encoded})\n" if frames else "pass\n")
        ),
        cases=[{"identifier": "probe", "input_json": {}, "expected_output": "7"}],
    )

    assert result["status"] == "RUNTIME_ERROR", (
        f"attack=result-{name} language=python expected=RUNTIME_ERROR actual={result['status']}"
    )
    await _assert_service_survives(f"result-{name}")


async def test_candidate_values_cannot_reorder_trusted_testcase_identities() -> None:
    values = b"2\n1\n"
    result = _raw_execute(
        harness=(
            f"import os\nfd = int(os.environ['COUNTERQ_RESULT_FD'])\nos.write(fd, {values!r})\n"
        ),
        cases=[
            {"identifier": "first", "input_json": {}, "expected_output": "1"},
            {"identifier": "second", "input_json": {}, "expected_output": "2"},
        ],
    )
    assert result["status"] == "SUCCEEDED"
    assert [case["identifier"] for case in result["cases"]] == ["first", "second"]
    assert [case["actual_output"] for case in result["cases"]] == ["2", "1"]
    assert [case["status"] for case in result["cases"]] == ["FAILED", "FAILED"]


async def test_same_uid_candidate_cannot_signal_sandbox_controller() -> None:
    outcome = await _execute(
        "python",
        """
import os, signal
class Solution:
    def solve(self, value):
        try:
            os.kill(os.getppid(), signal.SIGKILL)
            return -1
        except PermissionError:
            return value
""",
    )
    assert outcome.status == "SUCCEEDED", (
        f"attack=kill-controller language=python actual={outcome.status}"
    )
    assert outcome.cases[0].status == "PASSED"
    await _assert_service_survives("kill-controller")


async def test_background_descendant_is_denied_or_reaped_without_delaying_service() -> None:
    started = time.monotonic()
    outcome = await _execute(
        "python",
        """
import subprocess
class Solution:
    def solve(self, value):
        subprocess.Popen(["/bin/sh", "-c", "sleep 30"])
        return value
""",
    )
    elapsed = time.monotonic() - started
    assert outcome.status in {"SUCCEEDED", "RUNTIME_ERROR"}, (
        f"attack=background-child language=python expected=denied-or-reaped actual={outcome.status}"
    )
    if outcome.status == "SUCCEEDED":
        assert outcome.cases[0].status == "PASSED"
    else:
        assert any(
            message in outcome.stderr
            for message in ("Resource temporarily unavailable", "Operation not permitted")
        )
    assert elapsed < DEFAULT_RUN_TIMEOUT_SECONDS, f"attack=background-child elapsed={elapsed}"
    await _assert_service_survives("background-child")


async def test_candidate_exec_replacement_cannot_bypass_missing_result_failure() -> None:
    outcome = await _execute(
        "python",
        """
import os
class Solution:
    def solve(self, value):
        os.execv("/bin/true", ["true"])
""",
    )
    assert outcome.status == "RUNTIME_ERROR", (
        f"attack=exec-replacement language=python expected=RUNTIME_ERROR actual={outcome.status}"
    )
    await _assert_service_survives("exec-replacement")


async def test_sleeping_past_deadline_times_out_and_service_recovers() -> None:
    request = replace(
        _request(
            "python",
            "import time\nclass Solution:\n    def solve(self, value):\n        time.sleep(30)",
        ),
        run_timeout_seconds=1,
    )
    outcome = await LocalSandboxExecutorProvider(SANDBOX_URL).execute(request)
    assert outcome.status == "TIMED_OUT", (
        f"attack=sleep-timeout language=python expected=TIMED_OUT actual={outcome.status}"
    )
    await _assert_service_survives("sleep-timeout")


async def test_java_thread_exhaustion_is_bounded_and_service_recovers() -> None:
    request = replace(
        _request(
            "java",
            """
class Solution { public int solve(int value) {
    while (true) {
        new Thread(() -> { while (true) {} }).start();
    }
} }
""",
        ),
        run_timeout_seconds=2,
    )
    outcome = await LocalSandboxExecutorProvider(SANDBOX_URL).execute(request)
    assert outcome.status in {"RUNTIME_ERROR", "TIMED_OUT"}, (
        "attack=thread-exhaustion language=java "
        f"expected=RUNTIME_ERROR-or-TIMED_OUT actual={outcome.status}"
    )
    await _assert_service_survives("thread-exhaustion")


@pytest.mark.parametrize(
    ("language", "source"),
    [
        pytest.param(
            "cpp",
            "class Solution { public: int solve(int value) { (void)value; for (;;) {} } };",
            id="cpp-cpu-loop",
        ),
        pytest.param(
            "python",
            "class Solution:\n    def solve(self, value):\n        while True: pass",
            id="python-cpu-loop",
        ),
        pytest.param(
            "java",
            "class Solution { public int solve(int value) { while (true) {} } }",
            id="java-cpu-loop",
        ),
    ],
)
async def test_timeout_kills_process_tree_and_service_recovers(language: str, source: str) -> None:
    request = replace(_request(language, source), run_timeout_seconds=1)
    outcome = await LocalSandboxExecutorProvider(SANDBOX_URL).execute(request)
    assert outcome.status == "TIMED_OUT", (
        f"attack=cpu-timeout language={language} expected=TIMED_OUT actual={outcome.status}"
    )
    await _assert_service_survives(f"cpu-timeout-{language}")


@pytest.mark.parametrize(
    ("stream", "source"),
    [
        pytest.param(
            "stdout",
            "class Solution:\n    def solve(self, value):\n        print('COUNTERQ_CASE\\t1\\t7' * 20000)\n        return value",
            id="stdout-protocol-looking",
        ),
        pytest.param(
            "stderr",
            "import sys\nclass Solution:\n    def solve(self, value):\n        print('x' * 200000, file=sys.stderr)\n        return value",
            id="stderr",
        ),
    ],
)
async def test_output_flood_is_bounded_and_process_tree_terminated(
    stream: str, source: str
) -> None:
    request = replace(_request("python", source), output_limit_bytes=4096)
    outcome = await LocalSandboxExecutorProvider(SANDBOX_URL).execute(request)
    assert outcome.status == "OUTPUT_LIMIT_EXCEEDED", (
        f"attack={stream}-flood language=python expected=OUTPUT_LIMIT_EXCEEDED "
        f"actual={outcome.status}"
    )
    assert outcome.output_truncated
    assert len(outcome.stdout.encode()) + len(outcome.stderr.encode()) <= 4096
    await _assert_service_survives(f"{stream}-flood")


async def test_landlock_allows_run_workspace_but_denies_shared_and_immutable_paths() -> None:
    outcome = await _execute(
        "python",
        """
class Solution:
    def solve(self, value):
        blocked = 0
        for path in ("/tmp/counterq-stage3d-escape", "/srv/sandbox/app.py"):
            try:
                with open(path, "wb") as target:
                    target.write(b"hostile")
            except OSError:
                blocked += 1
        with open("candidate-local.bin", "wb") as target:
            target.write(b"ephemeral")
        return value if blocked == 2 else -1
""",
    )
    assert outcome.status == "SUCCEEDED", (
        f"attack=filesystem-escape language=python actual={outcome.status} "
        f"stderr={outcome.stderr!r}"
    )
    assert outcome.cases[0].status == "PASSED"
    await _assert_service_survives("filesystem-escape")


async def test_candidate_environment_contains_no_application_or_provider_secrets() -> None:
    outcome = await _execute(
        "python",
        """
import os
class Solution:
    def solve(self, value):
        forbidden = (
            "OPENAI_API_KEY",
            "DATABASE_URL",
            "REDIS_URL",
            "COUNTERQ_RUNNER_CONFIG",
        )
        if any(os.getenv(name) for name in forbidden):
            return -1
        try:
            with open("/proc/1/environ", "rb") as process_environment:
                process_environment.read(1)
        except OSError:
            return value
        return -1
""",
    )
    assert outcome.status == "SUCCEEDED", (
        f"attack=environment-secret-read language=python actual={outcome.status}"
    )
    assert outcome.cases[0].status == "PASSED"


async def test_workspace_flood_is_stopped_and_does_not_impair_later_runs() -> None:
    outcome = await _execute(
        "python",
        """
class Solution:
    def solve(self, value):
        chunk = b"x" * (1024 * 1024)
        for index in range(100):
            with open(f"flood-{index}", "wb") as target:
                target.write(chunk)
        while True:
            pass
""",
    )
    assert outcome.status == "RUNTIME_ERROR", (
        f"attack=workspace-flood language=python expected=RUNTIME_ERROR actual={outcome.status}"
    )
    await _assert_service_survives("workspace-flood")


@pytest.mark.parametrize(
    ("language", "source", "expected"),
    [
        pytest.param(
            "cpp",
            "class Solution { public: int solve(int value) { return value; } }; int main() {}",
            "COMPILE_ERROR",
            id="cpp-conflicting-main",
        ),
        pytest.param(
            "python",
            "class Solution:\n    def solve(self, value):\n        return 'wrong-type'",
            "RUNTIME_ERROR",
            id="python-wrong-return",
        ),
        pytest.param(
            "java",
            "class Main {} class Solution { public int solve(int value) { return value; } }",
            "COMPILE_ERROR",
            id="java-main-collision",
        ),
    ],
)
async def test_compile_harness_collisions_and_wrong_returns_fail_closed(
    language: str, source: str, expected: str
) -> None:
    outcome = await _execute(language, source)
    assert outcome.status == expected, (
        f"attack=compile-or-return language={language} expected={expected} actual={outcome.status}"
    )


async def test_successful_exit_without_trusted_result_is_runtime_error() -> None:
    outcome = await _execute(
        "python",
        "import os\nclass Solution:\n    def solve(self, value):\n        os._exit(0)",
    )
    assert outcome.status == "RUNTIME_ERROR", (
        f"attack=early-exit language=python expected=RUNTIME_ERROR actual={outcome.status}"
    )
    await _assert_service_survives("early-exit")


async def test_capacity_saturation_rejects_third_request_and_recovers() -> None:
    slow = replace(
        _request(
            "python", "class Solution:\n    def solve(self, value):\n        while True: pass"
        ),
        run_timeout_seconds=2,
    )
    async with httpx.AsyncClient(timeout=15) as client:
        first = asyncio.create_task(client.post(f"{SANDBOX_URL}/execute", json=_payload(slow)))
        second = asyncio.create_task(client.post(f"{SANDBOX_URL}/execute", json=_payload(slow)))
        await asyncio.sleep(0.35)
        third = await client.post(f"{SANDBOX_URL}/execute", json=_payload(slow))
        first_response, second_response = await asyncio.gather(first, second)
    for response in (first_response, second_response, third):
        response.raise_for_status()
    assert third.json()["status"] == "PROVIDER_ERROR", (
        f"attack=capacity-saturation expected=PROVIDER_ERROR actual={third.json()['status']}"
    )
    assert {first_response.json()["status"], second_response.json()["status"]} == {"TIMED_OUT"}
    await _assert_service_survives("capacity-saturation")


def test_sandbox_health_is_ready_before_adversarial_gate() -> None:
    response = httpx.get(f"{SANDBOX_URL}/health", timeout=5)
    response.raise_for_status()
    assert response.json()["status"] == "READY"
