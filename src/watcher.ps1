<#
  watcher.ps1 - 추적 디렉토리에 FileSystemWatcher 를 걸어 변경 시 자동 스냅샷.

  원리:
    - config.json(tracked)에서 감시할 부모 디렉토리들을 뽑는다.
    - 각 디렉토리에 FileSystemWatcher(Created/Changed/Renamed/Deleted) 등록.
    - 이벤트는 디바운스(기본 2초)되어 한 번의 'cas.py snapshot' 호출로 합쳐진다.
      (단순/확실: 이벤트 폭주를 타이머로 모아 1회 스냅샷)

  사용:
    powershell -ExecutionPolicy Bypass -File .\watcher.ps1
    powershell -File .\watcher.ps1 -Store "D:\.claude-snapshot" -DebounceMs 2000

  주의: 이 창이 떠 있는 동안만 동작(상주 watcher). Ctrl+C 로 종료.
#>
param(
  [string]$Store = $(if ($env:CLAUDE_SNAPSHOT_STORE) { $env:CLAUDE_SNAPSHOT_STORE } else { "D:\.claude-snapshot" }),
  [int]$DebounceMs = 2000,
  [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Cas = Join-Path $ScriptDir "cas.py"
$ConfigPath = Join-Path $Store "config.json"

if (-not (Test-Path $ConfigPath)) {
  Write-Host "config.json 없음: $ConfigPath  — 먼저 'cas.py init' 후 'track' 하세요." -ForegroundColor Yellow
  exit 1
}

$config = Get-Content $ConfigPath -Raw | ConvertFrom-Json

# tracked 항목에서 감시할 디렉토리 집합 도출 (파일이면 부모, 디렉토리/glob 이면 베이스 디렉토리)
$dirs = New-Object System.Collections.Generic.HashSet[string]
foreach ($t in $config.tracked) {
  $expanded = [Environment]::ExpandEnvironmentVariables($t)
  if (Test-Path $expanded -PathType Container) {
    [void]$dirs.Add((Resolve-Path $expanded).Path)
  } elseif (Test-Path $expanded -PathType Leaf) {
    [void]$dirs.Add((Split-Path -Parent (Resolve-Path $expanded).Path))
  } else {
    # glob 또는 미존재: 와일드카드/파일명 앞의 디렉토리 부분만 추출
    $base = Split-Path -Parent $expanded
    if ($base -and (Test-Path $base -PathType Container)) { [void]$dirs.Add((Resolve-Path $base).Path) }
  }
}

if ($dirs.Count -eq 0) {
  Write-Host "감시할 디렉토리를 찾지 못함. config.tracked 를 확인하세요." -ForegroundColor Yellow
  exit 1
}

Write-Host "watcher 시작 - 저장소: $Store" -ForegroundColor Cyan
$dirs | ForEach-Object { Write-Host "  감시: $_" }

# 상태 파일: MCP 서버의 watcher_status 가 살아있음/heartbeat 를 판단하는 근거.
$StatePath = Join-Path $Store "watcher.json"
function Write-WatcherState([string]$lastEvent) {
  $state = [ordered]@{
    pid        = $PID
    started    = (Get-Date).ToString("o")
    heartbeat  = (Get-Date).ToString("o")
    debounceMs = $DebounceMs
    dirs       = @($dirs)
    lastEvent  = $lastEvent
  }
  # Set-Content -Encoding UTF8 은 BOM 을 붙여 Python json.load(utf-8) 가 깨진다. BOM 없는 UTF-8 로.
  [System.IO.File]::WriteAllText($StatePath, ($state | ConvertTo-Json -Compress),
    (New-Object System.Text.UTF8Encoding($false)))
}
Write-WatcherState ""

# 디바운스용 공유 상태
$global:pending = $false
$global:lastEvent = ""

$action = {
  $global:pending = $true
  $global:lastEvent = "$($EventArgs.ChangeType): $($EventArgs.FullPath)"
}

$watchers = @()
foreach ($d in $dirs) {
  $fsw = New-Object System.IO.FileSystemWatcher $d
  $fsw.IncludeSubdirectories = $true
  $fsw.EnableRaisingEvents = $true
  $fsw.NotifyFilter = [System.IO.NotifyFilters]'FileName, LastWrite, DirectoryName'
  Register-ObjectEvent $fsw Created -Action $action | Out-Null
  Register-ObjectEvent $fsw Changed -Action $action | Out-Null
  Register-ObjectEvent $fsw Deleted -Action $action | Out-Null
  Register-ObjectEvent $fsw Renamed -Action $action | Out-Null
  $watchers += $fsw
}

Write-Host "감시 중... (Ctrl+C 로 종료)" -ForegroundColor Green
try {
  while ($true) {
    Start-Sleep -Milliseconds $DebounceMs
    if ($global:pending) {
      $global:pending = $false
      $msg = "auto: $global:lastEvent"
      Write-Host "[$(Get-Date -Format HH:mm:ss)] 변경 감지 -> 스냅샷  ($global:lastEvent)" -ForegroundColor DarkGray
      & $Python $Cas --store $Store snapshot -m $msg
    }
    Write-WatcherState $global:lastEvent  # heartbeat 갱신(살아있음 신호)
  }
} finally {
  $watchers | ForEach-Object { $_.EnableRaisingEvents = $false; $_.Dispose() }
  Get-EventSubscriber | Unregister-Event
  if (Test-Path $StatePath) { Remove-Item $StatePath -Force -ErrorAction SilentlyContinue }
  Write-Host "watcher 종료." -ForegroundColor Cyan
}
