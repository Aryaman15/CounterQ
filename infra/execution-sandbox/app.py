"""The only process allowed to compile/run candidate C++ in local development."""

from __future__ import annotations

import os
import resource
import selectors
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI(title="CounterQ local C++ sandbox")


class Case(BaseModel):
    identifier: str
    input_json: dict[str, object]
    expected_output: str
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


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "READY", "compiler": "g++"}


@app.post("/execute")
def execute(request: ExecuteRequest) -> dict[str, object]:
    if request.language != "cpp":
        return {"status": "PROVIDER_ERROR", "provider_run_id": None, "stderr": "Unsupported language"}
    run_id = str(uuid4())
    with tempfile.TemporaryDirectory(prefix="counterq-") as directory:
        workdir = Path(directory)
        source = workdir / "candidate.cpp"
        executable = workdir / "candidate"
        # This wrapper is trusted executor infrastructure. The persisted candidate
        # snapshot remains the exact source submitted in ``request.source_code``.
        source.write_text(
            "#include <bits/stdc++.h>\nusing namespace std;\n\n"
            + request.source_code
            + "\n"
            + request.harness,
            encoding="utf-8",
        )
        compile_result = _run(
            ["g++", "-std=c++17", "-O2", "-pipe", str(source), "-o", str(executable)],
            timeout=request.compile_timeout_seconds,
            # C++ compilation needs a larger bounded envelope than the candidate
            # binary itself; the stricter request limit is applied to execution.
            memory_limit_mb=max(request.memory_limit_mb, 512),
            output_limit=request.output_limit_bytes,
            process_limit=96,
            cwd=workdir,
        )
        if compile_result["timed_out"]:
            return {"status": "TIMED_OUT", "provider_run_id": run_id, "compiler_output": compile_result["stderr"], "timed_out": True}
        if compile_result["exit_code"] != 0:
            return {"status": "COMPILE_ERROR", "provider_run_id": run_id, "compiler_output": compile_result["stderr"], "output_truncated": compile_result["truncated"]}
        run_result = _run(
            [str(executable)],
            timeout=request.run_timeout_seconds,
            memory_limit_mb=request.memory_limit_mb,
            output_limit=request.output_limit_bytes,
            process_limit=8,
            cwd=workdir,
        )
        if run_result["timed_out"]:
            return {"status": "TIMED_OUT", "provider_run_id": run_id, "stdout": run_result["stdout"], "stderr": run_result["stderr"], "timed_out": True, "output_truncated": run_result["truncated"]}
        if run_result["truncated"]:
            return {"status": "OUTPUT_LIMIT_EXCEEDED", "provider_run_id": run_id, "stdout": run_result["stdout"], "stderr": run_result["stderr"], "exit_code": run_result["exit_code"], "output_truncated": True}
        if run_result["exit_code"] != 0:
            return {"status": "RUNTIME_ERROR", "provider_run_id": run_id, "stdout": run_result["stdout"], "stderr": run_result["stderr"], "exit_code": run_result["exit_code"]}
        actual = _case_outputs(run_result["stdout"], len(request.cases))
        cases = [
            {
                "identifier": case.identifier,
                "actual_output": actual[index],
                "status": "PASSED" if actual[index] == case.expected_output else "FAILED",
                "duration_ms": run_result["duration_ms"],
                "failure_classification": None if actual[index] == case.expected_output else "VISIBLE_CASE_MISMATCH",
            }
            for index, case in enumerate(request.cases)
        ]
        return {"status": "SUCCEEDED", "provider_run_id": run_id, "stdout": run_result["stdout"], "stderr": run_result["stderr"], "exit_code": 0, "duration_ms": run_result["duration_ms"], "memory_bytes": None, "cases": cases}


def _limits(memory_limit_mb: int, process_limit: int):
    def apply() -> None:
        memory = memory_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
        resource.setrlimit(resource.RLIMIT_CPU, (3, 3))
        resource.setrlimit(resource.RLIMIT_NPROC, (process_limit, process_limit))
        resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
        os.environ.clear()
        os.environ["PATH"] = "/usr/bin:/bin"
    return apply


def _run(command: list[str], *, timeout: int, memory_limit_mb: int, output_limit: int, process_limit: int, cwd: Path) -> dict[str, object]:
    started = time.monotonic()
    process = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, preexec_fn=_limits(memory_limit_mb, process_limit))
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    output = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = False
    timed_out = False
    while selector.get_map():
        if time.monotonic() - started > timeout:
            timed_out = True
            process.kill()
        for key, _ in selector.select(0.05):
            chunk = os.read(key.fileobj.fileno(), 4096)
            if not chunk:
                selector.unregister(key.fileobj)
                continue
            target = output[key.data]
            remaining = output_limit - len(target)
            target.extend(chunk[:max(remaining, 0)])
            if len(chunk) > remaining:
                truncated = True
                process.kill()
    process.wait(timeout=1)
    return {"stdout": output["stdout"].decode(errors="replace"), "stderr": output["stderr"].decode(errors="replace"), "exit_code": process.returncode, "timed_out": timed_out, "truncated": truncated, "duration_ms": int((time.monotonic() - started) * 1000)}


def _case_outputs(stdout: str, count: int) -> list[str | None]:
    outputs: list[str | None] = [None] * count
    for line in stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0] == "COUNTERQ_CASE" and parts[1].isdigit():
            index = int(parts[1]) - 1
            if 0 <= index < count:
                outputs[index] = parts[2]
    return outputs
