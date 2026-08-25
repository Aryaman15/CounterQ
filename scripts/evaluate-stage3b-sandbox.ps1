$ErrorActionPreference = "Stop"

function Invoke-Case([string]$Language, [string]$Source, [string]$Harness, [string]$Expected) {
  $body = @{ language=$Language; source_code=$Source; harness=$Harness; cases=@(@{identifier="visible-1";input_json=@{s="abcabcbb"};expected_output=$Expected}); compile_timeout_seconds=8; run_timeout_seconds=3; memory_limit_mb=384; output_limit_bytes=65536 } | ConvertTo-Json -Depth 5
  return Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8010/execute" -ContentType "application/json" -Body $body
}

$python = Invoke-Case "python" 'class Solution:
    def lengthOfLongestSubstring(self, s: str) -> int:
        return len(s)' 'solution = Solution()
print(f"COUNTERQ_CASE\t1\t{solution.lengthOfLongestSubstring("abcabcbb")}")' "8"
$java = Invoke-Case "java" 'class Solution { public int lengthOfLongestSubstring(String s) { return s.length(); } }' 'public class Main { public static void main(String[] args) { Solution s = new Solution(); System.out.println("COUNTERQ_CASE\t1\t" + s.lengthOfLongestSubstring("abcabcbb")); } }' "8"
if ($python.status -ne "SUCCEEDED" -or $java.status -ne "SUCCEEDED") { throw "Python/Java success parity failed" }
"python=$($python.status) java=$($java.status)"
