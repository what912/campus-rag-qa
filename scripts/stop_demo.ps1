[CmdletBinding()]
param([switch]$Quiet)

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$statePath = Join-Path $projectRoot 'data\share-state.json'
$configPath = Join-Path $projectRoot 'data\share-access.json'
$proxyScript = Join-Path $projectRoot 'scripts\share_proxy.py'
$knownHosts = Join-Path $projectRoot '.tools\share\known_hosts'
$utf8 = New-Object Text.UTF8Encoding($false)

function Get-OwnedProcess($ProcessId, [string]$Kind) {
    if (-not $ProcessId -or [int64]$ProcessId -le 0) { return $null }
    $process = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int64]$ProcessId) -ErrorAction SilentlyContinue
    if (-not $process -or -not $process.CommandLine) { return $null }
    $command = [string]$process.CommandLine
    if ($Kind -eq 'proxy') {
        if ($command.IndexOf($proxyScript, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
            $command.IndexOf($configPath, [StringComparison]::OrdinalIgnoreCase) -lt 0 -or
            $command -notmatch '--port(?:\s+|=)8767(?:\s|$)') { return $null }
    } else {
        if ($process.Name -notmatch '^ssh(?:\.exe)?$' -or
            $command -notmatch '(?:^|\s)"?nokey@localhost\.run"?(?:\s|$)' -or
            $command -notmatch '80:127\.0\.0\.1:8767(?:"|\s|$)' -or
            $command.IndexOf($knownHosts, [StringComparison]::OrdinalIgnoreCase) -lt 0) { return $null }
    }
    return $process
}

function Write-JsonAtomic([string]$Path, $Value) {
    $temporary = $Path + '.tmp.' + $PID
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 8), $utf8)
    if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($temporary, $Path, [System.Management.Automation.Language.NullString]::Value) }
    else { [IO.File]::Move($temporary, $Path) }
}

if (-not (Test-Path -LiteralPath $statePath)) {
    if (-not $Quiet) { Write-Output '没有记录中的公网演示；本机毕设服务未受影响。' }
    return
}
$state = [IO.File]::ReadAllText($statePath) | ConvertFrom-Json
$skipped = @()
foreach ($entry in @(@{field='tunnel_pid';kind='tunnel'}, @{field='proxy_pid';kind='proxy'})) {
    $processId = $state.($entry.field)
    if (-not $processId) { continue }
    $owned = Get-OwnedProcess $processId $entry.kind
    if ($owned) {
        # A venv executable can be a launcher for a native Python child. Check
        # each direct child against the same script/config/port contract before
        # stopping it. This is deliberately not a blanket process-tree kill.
        if ($entry.kind -eq 'proxy') {
            foreach ($child in @(Get-CimInstance Win32_Process -Filter ('ParentProcessId = ' + $owned.ProcessId) -ErrorAction SilentlyContinue)) {
                $ownedChild = Get-OwnedProcess $child.ProcessId 'proxy'
                if ($ownedChild) { Stop-Process -Id ([int]$ownedChild.ProcessId) -ErrorAction Stop }
            }
        }
        if (Get-OwnedProcess $owned.ProcessId $entry.kind) {
            Stop-Process -Id ([int]$owned.ProcessId) -ErrorAction Stop
        }
    } else {
        $stillExists = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int64]$processId) -ErrorAction SilentlyContinue
        if ($stillExists) { $skipped += [int64]$processId }
    }
    $state.($entry.field) = $null
}
if ($skipped.Count) {
    throw ('未停止无法验证归属的进程：' + ($skipped -join ', ') + '。本机毕设服务未受影响。')
}
if ($state.public_url) {
    if ($state.PSObject.Properties.Name -contains 'last_public_url') { $state.last_public_url = $state.public_url }
    else { $state | Add-Member -NotePropertyName last_public_url -NotePropertyValue $state.public_url }
}
$state.public_url = ''
if ($state.PSObject.Properties.Name -contains 'stopped_at') { $state.stopped_at = (Get-Date).ToUniversalTime().ToString('o') }
else { $state | Add-Member -NotePropertyName stopped_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) }
if ($state.PSObject.Properties.Name -contains 'updated_at') { $state.updated_at = (Get-Date).ToUniversalTime().ToString('o') }
else { $state | Add-Member -NotePropertyName updated_at -NotePropertyValue ((Get-Date).ToUniversalTime().ToString('o')) }
Write-JsonAtomic $statePath $state
if (Test-Path -LiteralPath $configPath) {
    $access = [IO.File]::ReadAllText($configPath) | ConvertFrom-Json
    if ($access.PSObject.Properties.Name -contains 'public_origin') {
        $access.public_origin = ''
        Write-JsonAtomic $configPath $access
    }
}
if (-not $Quiet) { Write-Output '公网演示已停止；本机 http://127.0.0.1:8765 继续运行。' }
