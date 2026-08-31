"""Trusted child launcher: apply per-process isolation before candidate tooling runs."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import resource
import signal
import sys
from pathlib import Path

_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_ADD_RULE = 445
_LANDLOCK_RESTRICT_SELF = 446
_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_LANDLOCK_EXECUTE = 1 << 0
_LANDLOCK_WRITE_FILE = 1 << 1
_LANDLOCK_READ_FILE = 1 << 2
_LANDLOCK_READ_DIR = 1 << 3
_LANDLOCK_REMOVE_DIR = 1 << 4
_LANDLOCK_REMOVE_FILE = 1 << 5
_LANDLOCK_MAKE_CHAR = 1 << 6
_LANDLOCK_MAKE_DIR = 1 << 7
_LANDLOCK_MAKE_REG = 1 << 8
_LANDLOCK_MAKE_SOCK = 1 << 9
_LANDLOCK_MAKE_FIFO = 1 << 10
_LANDLOCK_MAKE_BLOCK = 1 << 11
_LANDLOCK_MAKE_SYM = 1 << 12
_LANDLOCK_REFER = 1 << 13
_LANDLOCK_TRUNCATE = 1 << 14
_LANDLOCK_READ_ACCESS = _LANDLOCK_EXECUTE | _LANDLOCK_READ_FILE | _LANDLOCK_READ_DIR
_LANDLOCK_WRITE_ACCESS = (
    _LANDLOCK_WRITE_FILE
    | _LANDLOCK_REMOVE_DIR
    | _LANDLOCK_REMOVE_FILE
    | _LANDLOCK_MAKE_CHAR
    | _LANDLOCK_MAKE_DIR
    | _LANDLOCK_MAKE_REG
    | _LANDLOCK_MAKE_SOCK
    | _LANDLOCK_MAKE_FIFO
    | _LANDLOCK_MAKE_BLOCK
    | _LANDLOCK_MAKE_SYM
    | _LANDLOCK_REFER
    | _LANDLOCK_TRUNCATE
)
_LANDLOCK_HANDLED_ACCESS = _LANDLOCK_READ_ACCESS | _LANDLOCK_WRITE_ACCESS
_LIBC = ctypes.CDLL(None, use_errno=True)

_BLOCKED_SYSCALLS = (
    "socket",
    "socketpair",
    "connect",
    "bind",
    "listen",
    "accept",
    "accept4",
    "sendto",
    "sendmsg",
    "sendmmsg",
    "recvfrom",
    "recvmsg",
    "recvmmsg",
    "shutdown",
    "getsockname",
    "getpeername",
    "setsockopt",
    "getsockopt",
    "kill",
    "tkill",
    "tgkill",
    "pidfd_open",
    "pidfd_getfd",
    "pidfd_send_signal",
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "setsid",
    "setpgid",
    "unshare",
    "setns",
    "mount",
    "umount2",
    "pivot_root",
    "chroot",
    "bpf",
    "perf_event_open",
    "userfaultfd",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "open_by_handle_at",
    "name_to_handle_at",
    "keyctl",
    "add_key",
    "request_key",
)


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _LandlockPathBeneathAttr(ctypes.Structure):
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int),
    ]


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit(70)
    try:
        config = json.loads(os.environ.pop("COUNTERQ_RUNNER_CONFIG"))
        memory_mb = config["memory_mb"]
        cpu_seconds = int(config["cpu_seconds"])
        workdir = Path(config["workdir"])
        block_process_creation = bool(config["block_process_creation"])
        result_identifiers = tuple(str(value) for value in config["result_identifiers"])
        result_limit = int(config["result_limit"])
        trusted_result_fd_value = config["trusted_result_fd"]
        trusted_result_fd = (
            int(trusted_result_fd_value) if trusted_result_fd_value is not None else None
        )
        if result_identifiers:
            if trusted_result_fd is None:
                raise ValueError("trusted result descriptor is required")
            _supervise_candidate(
                command=sys.argv[1:],
                environment=os.environ,
                memory_mb=memory_mb,
                cpu_seconds=cpu_seconds,
                workdir=workdir,
                block_process_creation=block_process_creation,
                identifiers=result_identifiers,
                result_limit=result_limit,
                trusted_result_fd=trusted_result_fd,
            )
            return
        _apply_restrictions(memory_mb, cpu_seconds, workdir, block_process_creation)
        os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
    except (KeyError, TypeError, ValueError, OSError):
        raise SystemExit(70) from None


def _supervise_candidate(
    *,
    command: list[str],
    environment: dict[str, str],
    memory_mb: object,
    cpu_seconds: int,
    workdir: Path,
    block_process_creation: bool,
    identifiers: tuple[str, ...],
    result_limit: int,
    trusted_result_fd: int,
) -> None:
    candidate_read_fd, candidate_write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        try:
            os.close(candidate_read_fd)
            os.close(trusted_result_fd)
            os.set_inheritable(candidate_write_fd, True)
            child_environment = dict(environment)
            child_environment["COUNTERQ_RESULT_FD"] = str(candidate_write_fd)
            _apply_restrictions(memory_mb, cpu_seconds, workdir, block_process_creation)
            os.execvpe(command[0], command, child_environment)
        except (TypeError, ValueError, OSError):
            os._exit(70)
    os.close(candidate_write_fd)
    raw = bytearray()
    try:
        while True:
            chunk = os.read(candidate_read_fd, 4096)
            if not chunk:
                break
            remaining = result_limit - len(raw)
            raw.extend(chunk[: max(remaining, 0)])
            if len(chunk) > remaining:
                os.kill(child_pid, signal.SIGKILL)
                os.waitpid(child_pid, 0)
                raise SystemExit(70)
    finally:
        os.close(candidate_read_fd)
    _, wait_status = os.waitpid(child_pid, 0)
    if not os.WIFEXITED(wait_status) or os.WEXITSTATUS(wait_status) != 0:
        raise SystemExit(os.WEXITSTATUS(wait_status) if os.WIFEXITED(wait_status) else 128)
    values = raw.decode("utf-8", errors="strict").splitlines()
    if len(values) != len(identifiers):
        raise SystemExit(70)
    frames: list[bytes] = []
    for identifier, encoded_value in zip(identifiers, values, strict=True):
        json.loads(encoded_value)
        frames.append(f"COUNTERQ_RESULT\t{identifier}\t{encoded_value}\n".encode("utf-8"))
    _write_all(trusted_result_fd, b"".join(frames))
    os.close(trusted_result_fd)


def _apply_restrictions(
    memory_mb: object,
    cpu_seconds: int,
    workdir: Path,
    block_process_creation: bool,
) -> None:
    if memory_mb is not None:
        memory = int(memory_mb) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
    _install_landlock(workdir)
    _install_seccomp_filter(block_process_creation)


def _write_all(descriptor: int, value: bytes) -> None:
    offset = 0
    while offset < len(value):
        written = os.write(descriptor, value[offset:])
        if written <= 0:
            raise OSError(errno.EPIPE, "result channel closed")
        offset += written


def _install_landlock(workdir: Path) -> None:
    abi = _LIBC.syscall(
        _LANDLOCK_CREATE_RULESET,
        ctypes.c_void_p(),
        0,
        _LANDLOCK_CREATE_RULESET_VERSION,
    )
    if abi < 3:
        raise OSError(errno.ENOTSUP, "Landlock ABI 3 or newer is required")
    ruleset_attr = _LandlockRulesetAttr(_LANDLOCK_HANDLED_ACCESS)
    ruleset_fd = _LIBC.syscall(
        _LANDLOCK_CREATE_RULESET,
        ctypes.byref(ruleset_attr),
        ctypes.sizeof(ruleset_attr),
        0,
    )
    if ruleset_fd < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset failed")
    try:
        for path in ("/usr", "/usr/local", "/lib", "/lib64", "/etc", "/sys"):
            if os.path.exists(path):
                _add_landlock_path(ruleset_fd, Path(path), _LANDLOCK_READ_ACCESS)
        if os.path.exists("/dev"):
            _add_landlock_path(ruleset_fd, Path("/dev"), _LANDLOCK_READ_ACCESS)
        if os.path.exists("/dev/null"):
            _add_landlock_path(
                ruleset_fd,
                Path("/dev/null"),
                _LANDLOCK_READ_FILE | _LANDLOCK_WRITE_FILE,
            )
        _add_landlock_path(
            ruleset_fd,
            workdir,
            _LANDLOCK_READ_ACCESS | _LANDLOCK_WRITE_ACCESS,
        )
        if _LIBC.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS failed")
        if _LIBC.syscall(_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0) != 0:
            raise OSError(ctypes.get_errno(), "landlock_restrict_self failed")
    finally:
        os.close(ruleset_fd)


def _add_landlock_path(ruleset_fd: int, path: Path, allowed_access: int) -> None:
    path_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    try:
        rule = _LandlockPathBeneathAttr(allowed_access, path_fd)
        if (
            _LIBC.syscall(
                _LANDLOCK_ADD_RULE,
                ruleset_fd,
                _LANDLOCK_RULE_PATH_BENEATH,
                ctypes.byref(rule),
                0,
            )
            != 0
        ):
            raise OSError(ctypes.get_errno(), f"landlock_add_rule failed for {path}")
    finally:
        os.close(path_fd)


def _install_seccomp_filter(block_process_creation: bool) -> None:
    library = ctypes.CDLL("libseccomp.so.2", use_errno=True)
    library.seccomp_init.argtypes = [ctypes.c_uint32]
    library.seccomp_init.restype = ctypes.c_void_p
    library.seccomp_syscall_resolve_name.argtypes = [ctypes.c_char_p]
    library.seccomp_syscall_resolve_name.restype = ctypes.c_int
    library.seccomp_rule_add.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_uint32,
    ]
    library.seccomp_rule_add.restype = ctypes.c_int
    library.seccomp_load.argtypes = [ctypes.c_void_p]
    library.seccomp_load.restype = ctypes.c_int
    library.seccomp_release.argtypes = [ctypes.c_void_p]
    allow = 0x7FFF0000
    deny = 0x00050000 | errno.EPERM
    context = library.seccomp_init(allow)
    if not context:
        raise OSError(ctypes.get_errno(), "seccomp_init failed")
    try:
        blocked_syscalls = list(_BLOCKED_SYSCALLS)
        if block_process_creation:
            blocked_syscalls.extend(("clone", "clone3", "fork", "vfork"))
        for name in blocked_syscalls:
            syscall = library.seccomp_syscall_resolve_name(name.encode("ascii"))
            if syscall < 0:
                continue
            result = library.seccomp_rule_add(context, deny, syscall, 0)
            if result != 0:
                raise OSError(-result, f"seccomp_rule_add failed for {name}")
        result = library.seccomp_load(context)
        if result != 0:
            raise OSError(-result, "seccomp_load failed")
    finally:
        library.seccomp_release(context)


if __name__ == "__main__":
    main()
