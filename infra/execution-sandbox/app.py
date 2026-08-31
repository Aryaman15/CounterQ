"""The only local process allowed to compile/run untrusted candidate code."""

from __future__ import annotations

import os
import resource
import selectors
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="CounterQ local execution sandbox")
_capacity = threading.BoundedSemaphore(2)


class Case(BaseModel):
    identifier: str
    input_json: dict[str, object]
    expected_output: str | None
    visible: bool = True


class ExecuteRequest(BaseModel):
    language: str
    source_code: str = Field(max_length=200_000)
    harness: str = Field(max_length=100_000)
    cases: list[Case]
    compile_timeout_seconds: int = Field(ge=1, le=20)
    run_timeout_seconds: int = Field(ge=1, le=10)
    memory_limit_mb: int = Field(ge=64, le=512)
    output_limit_bytes: int = Field(ge=1024, le=131072)


class LanguageAdapter:
    language: str
    runtime_version: str
    compile_process_limit = 96
    run_process_limit = 8

    def prepare(self, workdir: Path, source: str, harness: str) -> None: ...

    def compile_command(self, workdir: Path) -> list[str] | None: ...

    def run_command(self, workdir: Path, memory_mb: int) -> list[str]: ...


class CppAdapter(LanguageAdapter):
    language, runtime_version = "cpp", "g++ C++17"

    def prepare(self, workdir: Path, source: str, harness: str) -> None:
        (workdir / "candidate.cpp").write_text(
            "#include <bits/stdc++.h>\nusing namespace std;\n\n" + source + "\n" + harness,
            encoding="utf-8",
        )

    def compile_command(self, workdir: Path) -> list[str]:
        return ["g++", "-std=c++17", "-O2", "-pipe", "candidate.cpp", "-o", "candidate"]

    def run_command(self, workdir: Path, memory_mb: int) -> list[str]:
        return [str(workdir / "candidate")]


class PythonAdapter(LanguageAdapter):
    language, runtime_version = "python", "Python 3"
    run_process_limit = 32

    def prepare(self, workdir: Path, source: str, harness: str) -> None:
        (workdir / "candidate.py").write_text(source + "\n" + harness, encoding="utf-8")

    def compile_command(self, workdir: Path) -> list[str]:
        return ["python3", "-m", "py_compile", "candidate.py"]

    def run_command(self, workdir: Path, memory_mb: int) -> list[str]:
        return ["python3", "-I", "candidate.py"]


class JavaAdapter(LanguageAdapter):
    language, runtime_version = "java", "OpenJDK 21"
    run_process_limit = 96

    def prepare(self, workdir: Path, source: str, harness: str) -> None:
        (workdir / "Solution.java").write_text(source, encoding="utf-8")
        (workdir / "Main.java").write_text(harness, encoding="utf-8")

    def compile_command(self, workdir: Path) -> list[str]:
        return ["javac", "-encoding", "UTF-8", "Solution.java", "Main.java"]

    def run_command(self, workdir: Path, memory_mb: int) -> list[str]:
        return [
            "java",
            f"-Xmx{max(memory_mb - 64, 64)}m",
            "-XX:MaxMetaspaceSize=64m",
            "-XX:ReservedCodeCacheSize=32m",
            "-cp",
            str(workdir),
            "Main",
        ]


_ADAPTERS: dict[str, LanguageAdapter] = {
    adapter.language: adapter for adapter in (CppAdapter(), PythonAdapter(), JavaAdapter())
}


@app.get("/health")
def health() -> dict[str, object]:
    languages = {
        language: _runtime_version(adapter)
        for language, adapter in _ADAPTERS.items()
    }
    return {
        "status": "READY" if all(languages.values()) else "UNAVAILABLE",
        "languages": languages,
    }


def _runtime_version(adapter: LanguageAdapter) -> str | None:
    command = {
        "cpp": ["g++", "--version"],
        "python": ["python3", "--version"],
        "java": ["java", "-version"],
    }[adapter.language]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=2, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    version = (result.stdout or result.stderr).splitlines()
    return version[0] if version else None


@app.post("/execute")
def execute(request: ExecuteRequest) -> dict[str, object]:
    adapter = _ADAPTERS.get(request.language)
    if adapter is None or not _capacity.acquire(blocking=False):
        return {"status": "PROVIDER_ERROR", "provider_run_id": None}
    try:
        return _execute(request, adapter)
    finally:
        _capacity.release()


def _execute(request: ExecuteRequest, adapter: LanguageAdapter) -> dict[str, object]:
    run_id = str(uuid4())
    with tempfile.TemporaryDirectory(prefix="counterq-") as directory:
        workdir = Path(directory)
        adapter.prepare(workdir, request.source_code, request.harness)
        compile_command = adapter.compile_command(workdir)
        if compile_command:
            compiled = _run(
                compile_command,
                timeout=request.compile_timeout_seconds,
                memory_mb=None if adapter.language == "java" else max(request.memory_limit_mb, 512),
                output_limit=request.output_limit_bytes,
                process_limit=adapter.compile_process_limit,
                cwd=workdir,
            )
            if compiled["timed_out"]:
                return _outcome("TIMED_OUT", run_id, runtime_version=adapter.runtime_version, compiler_output=compiled["stderr"], timed_out=True)
            if compiled["exit_code"] != 0:
                return _outcome("COMPILE_ERROR", run_id, runtime_version=adapter.runtime_version, compiler_output=compiled["stderr"], output_truncated=compiled["truncated"])
        ran = _run(
            adapter.run_command(workdir, request.memory_limit_mb),
            timeout=request.run_timeout_seconds,
            # JVM memory remains bounded by explicit JVM caps and the sandbox
            # container cgroup; RLIMIT_AS prevents normal JVM address-space setup.
            memory_mb=None if adapter.language == "java" else request.memory_limit_mb,
            output_limit=request.output_limit_bytes,
            process_limit=adapter.run_process_limit,
            cwd=workdir,
        )
        if ran["timed_out"]:
            return _outcome("TIMED_OUT", run_id, ran, runtime_version=adapter.runtime_version, timed_out=True)
        if ran["truncated"]:
            return _outcome("OUTPUT_LIMIT_EXCEEDED", run_id, ran, runtime_version=adapter.runtime_version, output_truncated=True)
        if ran["exit_code"] != 0:
            return _outcome("RUNTIME_ERROR", run_id, ran, runtime_version=adapter.runtime_version)
        actual = _case_outputs(ran["stdout"], len(request.cases))
        cases = [
            {
                "identifier": case.identifier,
                "actual_output": actual[index],
                "status": "PASSED"
                if case.expected_output is None or actual[index] == case.expected_output
                else "FAILED",
                "duration_ms": ran["duration_ms"],
                "failure_classification": None
                if case.expected_output is None or actual[index] == case.expected_output
                else "VISIBLE_CASE_MISMATCH",
            }
            for index, case in enumerate(request.cases)
        ]
        return _outcome("SUCCEEDED", run_id, ran, runtime_version=adapter.runtime_version, cases=cases)


def _outcome(status: str, run_id: str, result: dict[str, object] | None = None, **extra: object) -> dict[str, object]:
    result = result or {}
    return {"status": status, "provider_run_id": run_id, "stdout": result.get("stdout", ""), "stderr": result.get("stderr", ""), "exit_code": result.get("exit_code"), "duration_ms": result.get("duration_ms"), "memory_bytes": None, **extra}


def _limits(memory_mb: int | None, process_limit: int):
    def apply() -> None:
        if memory_mb is not None:
            memory = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
        resource.setrlimit(resource.RLIMIT_NPROC, (process_limit, process_limit))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        os.environ.clear()
        os.environ.update(PATH="/usr/bin:/bin", HOME="/tmp", TMPDIR="/tmp", LANG="C.UTF-8")
    return apply


def _run(command: list[str], *, timeout: int, memory_mb: int | None, output_limit: int, process_limit: int, cwd: Path) -> dict[str, object]:
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, preexec_fn=_limits(memory_mb, process_limit), start_new_session=True)
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output: dict[str, bytearray] = {"stdout": bytearray(), "stderr": bytearray()}
    timed_out = truncated = False
    while selector.get_map():
        if time.monotonic() - started > timeout:
            timed_out = True
            _terminate_tree(process)
        for key, _ in selector.select(0.05):
            chunk = os.read(key.fileobj.fileno(), 4096)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            target = output[key.data]
            remaining = output_limit - len(target)
            target.extend(chunk[: max(remaining, 0)])
            if len(chunk) > remaining:
                truncated = True
                _terminate_tree(process)
    process.wait(timeout=1)
    return {"stdout": output["stdout"].decode(errors="replace"), "stderr": output["stderr"].decode(errors="replace"), "exit_code": process.returncode, "timed_out": timed_out, "truncated": truncated, "duration_ms": int((time.monotonic() - started) * 1000)}


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGKILL)


def _case_outputs(stdout: str, count: int) -> list[str | None]:
    outputs: list[str | None] = [None] * count
    for line in stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0] == "COUNTERQ_CASE" and parts[1].isdigit():
            index = int(parts[1]) - 1
            if 0 <= index < count:
                outputs[index] = parts[2]
    return outputs
