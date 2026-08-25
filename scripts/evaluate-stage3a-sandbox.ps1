$ErrorActionPreference = "Stop"

function Invoke-SandboxCase {
  param(
    [string]$Name,
    [string]$Source,
    [string]$Harness = "int main() { return probe(); }",
    [int]$CompileTimeout = 8,
    [int]$RunTimeout = 2,
    [int]$MemoryLimit = 192,
    [int]$OutputLimit = 65536
  )

  $request = @{
    language = "cpp"
    source_code = $Source
    harness = $Harness
    cases = @()
    compile_timeout_seconds = $CompileTimeout
    run_timeout_seconds = $RunTimeout
    memory_limit_mb = $MemoryLimit
    output_limit_bytes = $OutputLimit
  } | ConvertTo-Json -Depth 5
  $result = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8010/execute" -ContentType "application/json" -Body $request
  return [PSCustomObject]@{ Name = $Name; Result = $result }
}

$health = Invoke-RestMethod "http://127.0.0.1:8010/health"
if ($health.status -ne "READY") { throw "Sandbox is not ready" }

$cases = @()
$cases += Invoke-SandboxCase "infinite-loop" "int probe() { for (;;) {} }" -RunTimeout 1
$cases += Invoke-SandboxCase "process-limit" "#include <unistd.h>`n#include <sys/wait.h>`nint probe() { int children = 0; for (int i = 0; i < 64; ++i) { int pid = fork(); if (pid == 0) _exit(0); if (pid > 0) ++children; } while (wait(nullptr) > 0) {} return children > 8 ? 1 : 0; }"
$cases += Invoke-SandboxCase "stdout-limit" "#include <iostream>`nint probe() { for (int i = 0; i < 200000; ++i) std::cout << 'x'; return 0; }" -OutputLimit 4096
$cases += Invoke-SandboxCase "memory-limit" "#include <vector>`nint probe() { std::vector<char> bytes(1024ULL * 1024ULL * 768ULL); return bytes[0]; }"
$cases += Invoke-SandboxCase "filesystem-probe" '#include <fstream>
int probe() { std::ifstream input("F:/Projects/CounterQ/.env"); return input.good() ? 1 : 0; }'
$cases += Invoke-SandboxCase "environment-probe" '#include <cstdlib>
int probe() { return std::getenv("OPENAI_API_KEY") ? 1 : 0; }'
$cases += Invoke-SandboxCase "network-probe" '#include <sys/socket.h>
#include <arpa/inet.h>
#include <unistd.h>
int probe() { int fd = socket(AF_INET, SOCK_STREAM, 0); sockaddr_in address{}; address.sin_family = AF_INET; address.sin_port = htons(443); inet_pton(AF_INET, "1.1.1.1", &address.sin_addr); int connected = connect(fd, reinterpret_cast<sockaddr*>(&address), sizeof(address)); close(fd); return connected == 0 ? 1 : 0; }' -RunTimeout 1
$cases += Invoke-SandboxCase "host-repository-probe" '#include <fstream>
int probe() { std::ifstream input("/workspace/.env"); return input.good() ? 1 : 0; }'
$longCompileSource = ((1..6000 | ForEach-Object { "int f$_() { return $_; }" }) -join "`n") + "`nint probe() { return f6000(); }"
$cases += Invoke-SandboxCase "long-compile" $longCompileSource -CompileTimeout 1
$cases += Invoke-SandboxCase "compiler-error" "int probe( {"
$cases += Invoke-SandboxCase "segmentation-fault" "#include <csignal>`nint probe() { raise(SIGSEGV); return 0; }"

$byName = @{}
foreach ($case in $cases) { $byName[$case.Name] = $case.Result }
if ($byName["infinite-loop"].status -ne "TIMED_OUT") { throw "Infinite loop was not timed out" }
if ($byName["stdout-limit"].status -ne "OUTPUT_LIMIT_EXCEEDED") { throw "Output limit was not enforced" }
if ($byName["memory-limit"].status -notin @("RUNTIME_ERROR", "TIMED_OUT")) { throw "Memory limit did not produce a bounded failure" }
if ($byName["long-compile"].status -ne "TIMED_OUT") { throw "Compile timeout was not enforced" }
if ($byName["filesystem-probe"].status -ne "SUCCEEDED" -or $byName["host-repository-probe"].status -ne "SUCCEEDED") { throw "Filesystem probes did not finish safely" }
if ($byName["environment-probe"].status -ne "SUCCEEDED" -or $byName["network-probe"].status -ne "SUCCEEDED") { throw "Environment/network probes did not finish safely" }
if ($byName["compiler-error"].status -ne "COMPILE_ERROR") { throw "Compiler failure was not classified" }
if ($byName["segmentation-fault"].status -ne "RUNTIME_ERROR") { throw "Segmentation fault was not classified" }

$cases | ForEach-Object { "{0}={1}" -f $_.Name, $_.Result.status }
