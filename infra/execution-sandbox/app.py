"""The only local process allowed to compile/run untrusted candidate code."""

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="CounterQ local execution sandbox")
_capacity = threading.BoundedSemaphore(2)
_SAFE_PATH = "/usr/local/bin:/usr/bin:/bin"
_MAX_WORKSPACE_BYTES = 64 * 1024 * 1024


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

    def prepare(self, workdir: Path, source: str, harness: str) -> None:
        (workdir / "candidate.py").write_text(source + "\n" + harness, encoding="utf-8")

    def compile_command(self, workdir: Path) -> list[str]:
        return ["python3", "-m", "py_compile", "candidate.py"]

    def run_command(self, workdir: Path, memory_mb: int) -> list[str]:
        return ["python3", "-I", "candidate.py"]


class JavaAdapter(LanguageAdapter):
    language, runtime_version = "java", "OpenJDK 21"

    def prepare(self, workdir: Path, source: str, harness: str) -> None:
        (workdir / "Solution.java").write_text(source, encoding="utf-8")
        (workdir / "Main.java").write_text(harness, encoding="utf-8")

    def compile_command(self, workdir: Path) -> list[str]:
        return ["javac", "-encoding", "UTF-8", "Solution.java", "Main.java"]

    def run_command(self, workdir: Path, memory_mb: int) -> list[str]:
        return [
            "java",
            "--add-opens=java.base/java.io=ALL-UNNAMED",
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
    languages = {language: _runtime_version(adapter) for language, adapter in _ADAPTERS.items()}
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
        try:
            return _execute(request, adapter)
        except (OSError, subprocess.SubprocessError) as exc:
            print(
                "sandbox execution boundary failure "
                f"type={type(exc).__name__} errno={getattr(exc, 'errno', None)}",
                flush=True,
            )
            return {"status": "PROVIDER_ERROR", "provider_run_id": None}
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
                cwd=workdir,
            )
            if compiled["timed_out"]:
                return _outcome(
                    "TIMED_OUT",
                    run_id,
                    runtime_version=adapter.runtime_version,
                    compiler_output=compiled["stderr"],
                    timed_out=True,
                )
            if compiled["exit_code"] != 0:
                return _outcome(
                    "COMPILE_ERROR",
                    run_id,
                    runtime_version=adapter.runtime_version,
                    compiler_output=compiled["stderr"],
                    output_truncated=compiled["truncated"],
                )
        ran = _run(
            adapter.run_command(workdir, request.memory_limit_mb),
            timeout=request.run_timeout_seconds,
            # JVM memory remains bounded by explicit JVM caps and the sandbox
            # container cgroup; RLIMIT_AS prevents normal JVM address-space setup.
            memory_mb=None if adapter.language == "java" else request.memory_limit_mb,
            output_limit=request.output_limit_bytes,
            cwd=workdir,
            result_identifiers=tuple(case.identifier for case in request.cases),
            block_process_creation=adapter.language != "java",
        )
        if ran["timed_out"]:
            return _outcome(
                "TIMED_OUT", run_id, ran, runtime_version=adapter.runtime_version, timed_out=True
            )
        if ran["truncated"]:
            return _outcome(
                "OUTPUT_LIMIT_EXCEEDED",
                run_id,
                ran,
                runtime_version=adapter.runtime_version,
                output_truncated=True,
            )
        if ran["workspace_exceeded"]:
            return _outcome("RUNTIME_ERROR", run_id, ran, runtime_version=adapter.runtime_version)
        if ran["exit_code"] != 0:
            return _outcome("RUNTIME_ERROR", run_id, ran, runtime_version=adapter.runtime_version)
        if ran["protocol_error"] is not None:
            return _outcome("RUNTIME_ERROR", run_id, ran, runtime_version=adapter.runtime_version)
        actual = ran["case_outputs"]
        assert isinstance(actual, dict)
        cases = [
            {
                "identifier": case.identifier,
                "actual_output": actual[case.identifier],
                "status": "PASSED"
                if case.expected_output is None or actual[case.identifier] == case.expected_output
                else "FAILED",
                "duration_ms": ran["duration_ms"],
                "failure_classification": None
                if case.expected_output is None or actual[case.identifier] == case.expected_output
                else "VISIBLE_CASE_MISMATCH",
            }
            for case in request.cases
        ]
        return _outcome(
            "SUCCEEDED", run_id, ran, runtime_version=adapter.runtime_version, cases=cases
        )


def _outcome(
    status: str, run_id: str, result: dict[str, object] | None = None, **extra: object
) -> dict[str, object]:
    result = result or {}
    return {
        "status": status,
        "provider_run_id": run_id,
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "exit_code": result.get("exit_code"),
        "duration_ms": result.get("duration_ms"),
        "memory_bytes": None,
        **extra,
    }


def _run(
    command: list[str],
    *,
    timeout: int,
    memory_mb: int | None,
    output_limit: int,
    cwd: Path,
    result_identifiers: tuple[str, ...] = (),
    block_process_creation: bool = False,
) -> dict[str, object]:
    started = time.monotonic()
    result_read_fd, result_write_fd = os.pipe()
    environment = {
        "PATH": _SAFE_PATH,
        "HOME": str(cwd),
        "TMPDIR": str(cwd),
        "LANG": "C.UTF-8",
    }
    environment["COUNTERQ_RUNNER_CONFIG"] = json.dumps(
        {
            "memory_mb": memory_mb,
            "cpu_seconds": timeout + 1,
            "workdir": str(cwd),
            "block_process_creation": block_process_creation,
            "result_identifiers": result_identifiers,
            "result_limit": output_limit,
            "trusted_result_fd": result_write_fd if result_identifiers else None,
        },
        separators=(",", ":"),
    )
    try:
        process = subprocess.Popen(
            [sys.executable, "/srv/sandbox/runner.py", *command],
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            start_new_session=True,
            pass_fds=(result_write_fd,) if result_identifiers else (),
            env=environment,
        )
    finally:
        os.close(result_write_fd)
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    if result_identifiers:
        selector.register(result_read_fd, selectors.EVENT_READ, "result")
    else:
        os.close(result_read_fd)
    output: dict[str, bytearray] = {
        "stdout": bytearray(),
        "stderr": bytearray(),
        "result": bytearray(),
    }
    timed_out = truncated = workspace_exceeded = False
    while selector.get_map():
        if time.monotonic() - started > timeout:
            timed_out = True
            _terminate_tree(process)
        elif process.poll() is not None:
            # A child may outlive the candidate entrypoint or close its copy of
            # the observed pipes. It never survives the authoritative process.
            _terminate_tree(process)
        elif _workspace_size(cwd) > _MAX_WORKSPACE_BYTES:
            workspace_exceeded = True
            _terminate_tree(process)
        for key, _ in selector.select(0.05):
            file_descriptor = key.fileobj if isinstance(key.fileobj, int) else key.fileobj.fileno()
            chunk = os.read(file_descriptor, 4096)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            target = output[key.data]
            if key.data == "result":
                observed = len(target)
            else:
                observed = len(output["stdout"]) + len(output["stderr"])
            remaining = output_limit - observed
            target.extend(chunk[: max(remaining, 0)])
            if len(chunk) > remaining:
                truncated = True
                _terminate_tree(process)
    selector.close()
    _terminate_tree(process)
    process.wait(timeout=1)
    result_text = output["result"].decode(errors="replace")
    case_outputs, protocol_error = _case_outputs(result_text, result_identifiers)
    return {
        "stdout": output["stdout"].decode(errors="replace"),
        "stderr": output["stderr"].decode(errors="replace"),
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "truncated": truncated,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "case_outputs": case_outputs,
        "protocol_error": protocol_error,
        "workspace_exceeded": workspace_exceeded,
    }


def _terminate_tree(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _case_outputs(
    result_channel: str, expected_identifiers: tuple[str, ...]
) -> tuple[dict[str, str], str | None]:
    expected = set(expected_identifiers)
    if len(expected) != len(expected_identifiers):
        return {}, "DUPLICATE_EXPECTED_CASE_IDENTIFIER"
    outputs: dict[str, str] = {}
    for line in result_channel.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or parts[0] != "COUNTERQ_RESULT":
            return {}, "MALFORMED_RESULT_FRAME"
        identifier, encoded_value = parts[1], parts[2]
        if identifier not in expected:
            return {}, "UNKNOWN_RESULT_IDENTIFIER"
        if identifier in outputs:
            return {}, "DUPLICATE_RESULT_IDENTIFIER"
        try:
            json.loads(encoded_value)
        except json.JSONDecodeError:
            return {}, "INVALID_RESULT_JSON"
        outputs[identifier] = encoded_value
    if set(outputs) != expected:
        return {}, "MISSING_RESULT_IDENTIFIER"
    return outputs, None


def _workspace_size(root: Path) -> int:
    total = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = os.scandir(directory)
        except OSError:
            continue
        with entries:
            for entry in entries:
                try:
                    metadata = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if S_ISDIR(metadata.st_mode):
                    pending.append(Path(entry.path))
                elif S_ISREG(metadata.st_mode):
                    total += metadata.st_size
                    if total > _MAX_WORKSPACE_BYTES:
                        return total
    return total
