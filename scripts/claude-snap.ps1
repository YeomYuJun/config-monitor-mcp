<#
  claude-snap.ps1 - cas.py / claude_config.py 의 얇은 CLI 래퍼.
  예)
    .\claude-snap.ps1 init
    .\claude-snap.ps1 track "D:\sample_project\input" "$env:APPDATA\Claude\claude_desktop_config.json"
    .\claude-snap.ps1 status
    .\claude-snap.ps1 snapshot -m "수동 백업"
    .\claude-snap.ps1 diff "D:\sample_project\input\foo.txt"
    .\claude-snap.ps1 config        # Claude 설정 카드 HTML 생성 후 열기
#>
param([Parameter(ValueFromRemainingArguments=$true)] $Args)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$SrcDir = Join-Path (Split-Path -Parent $ScriptDir) "src"
$Python = "python"

if ($Args.Count -ge 1 -and $Args[0] -eq "config") {
  $out = Join-Path $ScriptDir "claude-status.html"
  & $Python (Join-Path $SrcDir "claude_config.py") report -o $out
  if (Test-Path $out) { Start-Process $out }   # 기본 브라우저로 열기
} else {
  & $Python (Join-Path $SrcDir "cas.py") @Args
}
