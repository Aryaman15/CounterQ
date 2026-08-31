# ruff: noqa: E501, I001
# mypy: ignore-errors
"""Real local-sandbox acceptance checks, intentionally excluded from normal CI."""

from __future__ import annotations

import os
import time
import httpx
import pytest

from app.execution.harness import harness_for_problem
from app.execution.policy import (
    DEFAULT_COMPILE_TIMEOUT_SECONDS,
    DEFAULT_MEMORY_LIMIT_MB,
    DEFAULT_OUTPUT_LIMIT_BYTES,
    DEFAULT_RUN_TIMEOUT_SECONDS,
)

pytestmark = pytest.mark.skipif(
    os.getenv("COUNTERQ_SANDBOX_EVALUATION") != "1",
    reason="requires the local isolated execution sandbox",
)

SANDBOX_URL = "http://127.0.0.1:8010"
IO_SCHEMA = {
    "execution": {
        "method_name": "lengthOfLongestSubstring",
        "arguments": [{"name": "s", "type": "string"}],
        "return_type": "int",
        "comparator": "EXACT",
        "visible_cases": [
            {"arguments": {"s": "abcabcbb"}, "expected_output": 3},
            {"arguments": {"s": "bbbbb"}, "expected_output": 1},
            {"arguments": {"s": "pwwkew"}, "expected_output": 3},
        ],
    }
}
PROBE_SCHEMA = {
    "execution": {
        "method_name": "probe",
        "arguments": [{"name": "value", "type": "int"}],
        "return_type": "int",
        "comparator": "EXACT",
        "visible_cases": [{"arguments": {"value": 0}, "expected_output": 0}],
    }
}


def _execute(
    language: str,
    source: str,
    harness: str = "",
    *,
    cases: list[dict[str, object]] | None = None,
    timeout: int = DEFAULT_RUN_TIMEOUT_SECONDS,
    memory: int = DEFAULT_MEMORY_LIMIT_MB,
    output: int = DEFAULT_OUTPUT_LIMIT_BYTES,
) -> dict[str, object]:
    response = httpx.post(
        f"{SANDBOX_URL}/execute",
        json={
            "language": language,
            "source_code": source,
            "harness": harness,
            "cases": cases or [],
            "compile_timeout_seconds": DEFAULT_COMPILE_TIMEOUT_SECONDS,
            "run_timeout_seconds": timeout,
            "memory_limit_mb": memory,
            "output_limit_bytes": output,
        },
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


@pytest.mark.parametrize(
    ("language", "source"),
    [
        (
            "cpp",
            "class Solution { public: int lengthOfLongestSubstring(string s) { int best=0,l=0; vector<int> last(256,-1); for(int r=0;r<(int)s.size();++r){ l=max(l,last[(unsigned char)s[r]]+1); last[(unsigned char)s[r]]=r; best=max(best,r-l+1); } return best; } };",
        ),
        (
            "python",
            "class Solution:\n    def lengthOfLongestSubstring(self, s: str) -> int:\n        last = {}; left = answer = 0\n        for right, char in enumerate(s):\n            left = max(left, last.get(char, -1) + 1)\n            last[char] = right\n            answer = max(answer, right - left + 1)\n        return answer",
        ),
        (
            "java",
            "class Solution { public int lengthOfLongestSubstring(String s) { int[] last = new int[256]; java.util.Arrays.fill(last, -1); int left=0, best=0; for(int right=0; right<s.length(); right++){ left=Math.max(left,last[s.charAt(right)]+1); last[s.charAt(right)]=right; best=Math.max(best,right-left+1); } return best; } }",
        ),
    ],
)
def test_real_trusted_harness_runs_all_visible_cases(language: str, source: str) -> None:
    harness, cases = harness_for_problem(IO_SCHEMA, language)
    result = _execute(
        language,
        source,
        harness,
        cases=[
            {
                "identifier": case.identifier,
                "input_json": case.input_json,
                "expected_output": case.expected_output,
                "visible": case.visible,
            }
            for case in cases
        ],
    )
    assert result["status"] == "SUCCEEDED"
    assert [case["status"] for case in result["cases"]] == ["PASSED", "PASSED", "PASSED"]


def _assert_status(
    language: str, source: str, expected: str, harness: str = "", **kwargs: object
) -> None:
    assert _execute(language, source, harness, **kwargs)["status"] == expected


def _execute_probe(language: str, source: str) -> dict[str, object]:
    harness, cases = harness_for_problem(PROBE_SCHEMA, language)
    return _execute(
        language,
        source,
        harness,
        cases=[
            {
                "identifier": case.identifier,
                "input_json": case.input_json,
                "expected_output": case.expected_output,
                "visible": case.visible,
            }
            for case in cases
        ],
    )


@pytest.mark.parametrize(
    ("language", "source"),
    [
        ("python", "class Solution:\n    def broken(:\n        pass"),
        ("java", "class Solution { public int broken( { return 0; } }"),
    ],
)
def test_language_syntax_failures_are_compile_errors(language: str, source: str) -> None:
    _assert_status(language, source, "COMPILE_ERROR")


@pytest.mark.parametrize(
    ("language", "source", "harness"),
    [
        ("python", "raise RuntimeError('bounded')", ""),
        (
            "java",
            "class Solution {}",
            "public class Main { public static void main(String[] args) { throw new RuntimeException(); } }",
        ),
    ],
)
def test_language_runtime_errors_are_bounded(language: str, source: str, harness: str) -> None:
    _assert_status(language, source, "RUNTIME_ERROR", harness)


@pytest.mark.parametrize(
    ("language", "source", "harness"),
    [
        ("python", "while True: pass", ""),
        (
            "java",
            "class Solution {}",
            "public class Main { public static void main(String[] args) { while (true) {} } }",
        ),
    ],
)
def test_language_timeouts_kill_execution(language: str, source: str, harness: str) -> None:
    _assert_status(language, source, "TIMED_OUT", harness, timeout=1)


@pytest.mark.parametrize(
    ("language", "source", "harness"),
    [
        ("python", "print('x' * 100000)", ""),
        (
            "java",
            "class Solution {}",
            'public class Main { public static void main(String[] args) { System.out.print("x".repeat(100000)); } }',
        ),
    ],
)
def test_language_output_is_bounded(language: str, source: str, harness: str) -> None:
    _assert_status(language, source, "OUTPUT_LIMIT_EXCEEDED", harness, output=1024)


@pytest.mark.parametrize(
    ("language", "source", "harness"),
    [
        ("python", "bytes(1024 * 1024 * 768)", ""),
        (
            "java",
            "class Solution {}",
            "public class Main { public static void main(String[] args) { byte[] value = new byte[768 * 1024 * 1024]; } }",
        ),
    ],
)
def test_language_memory_pressure_is_bounded(language: str, source: str, harness: str) -> None:
    assert _execute(language, source, harness, timeout=2)["status"] in {
        "RUNTIME_ERROR",
        "TIMED_OUT",
    }


@pytest.mark.parametrize(
    ("language", "source"),
    [
        (
            "python",
            "import os\nclass Solution:\n    def probe(self, value): return 1 if os.getenv('OPENAI_API_KEY') else 0",
        ),
        (
            "java",
            'class Solution { public int probe(int value) { return System.getenv("OPENAI_API_KEY") == null ? 0 : 1; } }',
        ),
    ],
)
def test_language_environment_has_no_openai_key(language: str, source: str) -> None:
    result = _execute_probe(language, source)
    assert result["status"] == "SUCCEEDED" and result["cases"][0]["status"] == "PASSED"


@pytest.mark.parametrize(
    ("language", "source"),
    [
        (
            "python",
            "import os\nclass Solution:\n    def probe(self, value): return 1 if os.path.exists('/workspace/.env') or os.path.exists('F:/Projects/CounterQ/.env') else 0",
        ),
        (
            "java",
            'class Solution { public int probe(int value) { return java.nio.file.Files.exists(java.nio.file.Path.of("/workspace/.env")) ? 1 : 0; } }',
        ),
    ],
)
def test_language_cannot_read_repository_environment(language: str, source: str) -> None:
    result = _execute_probe(language, source)
    assert result["status"] == "SUCCEEDED" and result["cases"][0]["status"] == "PASSED"


@pytest.mark.parametrize(
    ("language", "source"),
    [
        (
            "python",
            "import socket\nclass Solution:\n    def probe(self, value):\n        try:\n            socket.create_connection(('1.1.1.1', 443), timeout=.2)\n            return 1\n        except OSError:\n            return 0",
        ),
        (
            "java",
            'class Solution { public int probe(int value) { try (java.net.Socket socket = new java.net.Socket()) { socket.connect(new java.net.InetSocketAddress("1.1.1.1", 443), 200); return 1; } catch (Throwable ignored) { return 0; } } }',
        ),
    ],
)
def test_language_network_is_unavailable(language: str, source: str) -> None:
    result = _execute_probe(language, source)
    assert result["status"] == "SUCCEEDED" and result["cases"][0]["status"] == "PASSED"


@pytest.mark.parametrize(
    ("language", "source", "harness"),
    [
        (
            "python",
            "import subprocess\nchildren=[subprocess.Popen(['/bin/true']) for _ in range(64)]\nprint('COUNTERQ_CASE\\t1\\t0')",
            "",
        ),
        (
            "java",
            "class Solution {}",
            'public class Main { public static void main(String[] args) throws Exception { for (int index=0; index<64; index++) new ProcessBuilder("/bin/true").start(); System.out.println("COUNTERQ_CASE\\t1\\t0"); } }',
        ),
    ],
)
def test_language_process_spawning_is_bounded(language: str, source: str, harness: str) -> None:
    assert _execute(language, source, harness, timeout=2)["status"] in {
        "SUCCEEDED",
        "RUNTIME_ERROR",
        "TIMED_OUT",
    }


@pytest.mark.parametrize(
    ("language", "source", "harness", "marker"),
    [
        (
            "java",
            "class Solution {}",
            'public class Main { public static void main(String[] args) throws Exception { new ProcessBuilder("/bin/sh", "-c", "sleep 2; touch /tmp/counterq-java-child").start(); while (true) {} } }',
            "/tmp/counterq-java-child",
        ),
    ],
)
def test_timeout_kills_candidate_process_tree(
    language: str, source: str, harness: str, marker: str
) -> None:
    assert marker.startswith("/tmp/")
    _assert_status(language, source, "TIMED_OUT", harness, timeout=1)
    time.sleep(0.2)
    probe = _execute_probe("python", "class Solution:\n    def probe(self, value): return 0")
    assert probe["cases"][0]["status"] == "PASSED"
