[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$dataDir = Join-Path $projectRoot 'data'
$outputDir = Join-Path $projectRoot 'output'
$toolDir = Join-Path $projectRoot '.tools\share'
$configPath = Join-Path $dataDir 'share-access.json'
$statePath = Join-Path $dataDir 'share-state.json'
$instructionPath = Join-Path $outputDir '临时公网访问说明.txt'
$proxyScript = Join-Path $projectRoot 'scripts\share_proxy.py'
$watcherScript = Join-Path $projectRoot 'scripts\share_watch.py'
$sshConfig = Join-Path $toolDir 'ssh_config'
$knownHosts = Join-Path $toolDir 'known_hosts'
$proxyPort = 8767
$upstream = 'http://127.0.0.1:8765'
$utf8 = New-Object Text.UTF8Encoding($false)
$startedInThisRun = $false

function Write-JsonAtomic([string]$Path, $Value) {
    $temporary = $Path + '.tmp.' + $PID
    [IO.File]::WriteAllText($temporary, ($Value | ConvertTo-Json -Depth 8), $utf8)
    if (Test-Path -LiteralPath $Path) {
        [IO.File]::Replace($temporary, $Path, [System.Management.Automation.Language.NullString]::Value)
    } else {
        [IO.File]::Move($temporary, $Path)
    }
}

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

function Get-HttpStatus([string]$Url) {
    try {
        return [int](Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2).StatusCode
    } catch {
        if ($_.Exception.Response) { return [int]$_.Exception.Response.StatusCode }
        return 0
    }
}

function Get-PublicUrl([string]$Text) {
    # Only tunnel-owned subdomains are accepted; never admin/docs URLs or a URL
    # with credentials, a path, a query, or an arbitrary third-party hostname.
    $pattern = 'https?://(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:lhr\.life|localhost\.run)(?::443)?(?![a-z0-9.:-])'
    $candidates = @()
    foreach ($match in [regex]::Matches($Text, $pattern, [Text.RegularExpressions.RegexOptions]::IgnoreCase)) {
        $uri = [Uri]$match.Value
        if ($uri.Host -in @('admin.localhost.run', 'www.localhost.run', 'docs.localhost.run')) { continue }
        if ($uri.UserInfo -or -not $uri.IsDefaultPort) { continue }
        $candidates += 'https://' + $uri.Host.ToLowerInvariant()
    }
    if ($candidates.Count) { return $candidates[-1] }
    return ''
}

function Read-RunningLog([string]$Path) {
    # Windows PowerShell's redirect writer keeps this file open. Share the
    # existing writer's access while inspecting the tunnel's event stream.
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    $reader = New-Object IO.StreamReader($stream, [Text.Encoding]::UTF8)
    try { return $reader.ReadToEnd() } finally { $reader.Dispose() }
}

function Write-Instructions([string]$Url, $Access) {
    $text = @"
知校 · 临时公网演示
生成时间：$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz')

访问网址：$Url
账号：$($Access.username)
密码：$($Access.password)

在其他电脑的浏览器输入网址，再在网页登录页填写以上账号和密码。
这份文件含访问口令，请只交给需要试用的人。

使用条件：本电脑保持开机、联网，毕设主服务及公网演示保持运行。
免费隧道的网址会变化；重新开启后请以这份文件中的新网址为准。
停止演示：双击项目根目录的“停止公网演示.cmd”，不会停止本机毕设服务。
"@
    [IO.File]::WriteAllText($instructionPath, $text, $utf8)
}

function Show-Access([string]$Url) {
    Write-Output ('演示网址：' + $Url)
    Write-Output ('账号和密码保存在：' + $instructionPath)
}

function Ensure-Watcher($State) {
    if (-not (Test-Path -LiteralPath $watcherScript)) { throw '缺少 scripts/share_watch.py。' }
    if ($State.watcher_pid) {
        $existing = Get-CimInstance Win32_Process -Filter ('ProcessId = ' + [int64]$State.watcher_pid) -ErrorAction SilentlyContinue
        if ($existing -and $existing.CommandLine -and
            $existing.CommandLine.IndexOf($watcherScript, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and
            $existing.CommandLine -match ('--tunnel-pid\s+' + [int64]$State.tunnel_pid + '(?:\s|$)')) { return }
    }
    $watchArgs = '"{0}" --state "{1}" --config "{2}" --log "{3}" --instructions "{4}" --tunnel-pid {5}' -f `
        $watcherScript,$statePath,$configPath,(Join-Path $outputDir 'share-tunnel.stdout.log'),$instructionPath,$State.tunnel_pid
    $watcher = Start-Process -FilePath (Join-Path $projectRoot '.venv\Scripts\python.exe') -ArgumentList $watchArgs `
        -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $outputDir 'share-watch.stdout.log') `
        -RedirectStandardError (Join-Path $outputDir 'share-watch.stderr.log') -PassThru
    if ($State -is [Collections.IDictionary]) { $State['watcher_pid'] = $watcher.Id }
    elseif ($State.PSObject.Properties.Name -contains 'watcher_pid') { $State.watcher_pid = $watcher.Id }
    else { $State | Add-Member -NotePropertyName watcher_pid -NotePropertyValue $watcher.Id }
    Write-JsonAtomic $statePath $State
}

try {
    foreach ($folder in @($dataDir, $outputDir, $toolDir)) {
        [void][IO.Directory]::CreateDirectory($folder)
    }
    if (-not (Test-Path -LiteralPath $proxyScript)) { throw '缺少 scripts/share_proxy.py。' }
    $health = Invoke-RestMethod -Uri ($upstream + '/api/health') -TimeoutSec 4
    if ($health.status -ne 'ok') { throw '本机毕设服务尚未就绪，请先运行“启动系统.cmd”。' }

    if (-not (Test-Path -LiteralPath $configPath)) {
        $bytes = New-Object byte[] 24
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $password = [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
        Write-JsonAtomic $configPath ([ordered]@{username='demo';password=$password;upstream=$upstream;public_origin='';cookie_auth=$true})
        $password = $null
    }
    $access = [IO.File]::ReadAllText($configPath) | ConvertFrom-Json
    if (-not $access.username -or [string]$access.username -match ':' -or
        -not $access.password -or ([string]$access.password).Length -lt 20 -or
        $access.upstream -ne $upstream) { throw '本机演示配置无效；未启动公网连接。' }
    if (-not ($access.PSObject.Properties.Name -contains 'public_origin')) {
        $access | Add-Member -NotePropertyName public_origin -NotePropertyValue ''
    }
    if (-not ($access.PSObject.Properties.Name -contains 'cookie_auth')) {
        $access | Add-Member -NotePropertyName cookie_auth -NotePropertyValue $true
    } else { $access.cookie_auth = $true }

    if (Test-Path -LiteralPath $statePath) {
        $oldState = [IO.File]::ReadAllText($statePath) | ConvertFrom-Json
        $oldProxy = Get-OwnedProcess $oldState.proxy_pid 'proxy'
        $oldTunnel = Get-OwnedProcess $oldState.tunnel_pid 'tunnel'
        $oldUrl = ''
        $existingLog = Join-Path $outputDir 'share-tunnel.stdout.log'
        if ($oldTunnel -and (Test-Path -LiteralPath $existingLog)) {
            $oldUrl = Get-PublicUrl (Read-RunningLog $existingLog)
        }
        if (-not $oldUrl) { $oldUrl = Get-PublicUrl ([string]$oldState.public_url) }
        if ($oldProxy -and $oldTunnel -and $oldUrl -and
            (Get-HttpStatus ('http://127.0.0.1:' + $proxyPort + '/')) -eq 401) {
            $access.public_origin = $oldUrl
            Write-JsonAtomic $configPath $access
            $oldState.public_url = $oldUrl
            $oldState.updated_at = (Get-Date).ToUniversalTime().ToString('o')
            Write-JsonAtomic $statePath $oldState
            Ensure-Watcher $oldState
            Write-Instructions $oldUrl $access
            Show-Access $oldUrl
            exit 0
        }
        # Clean up only positively identified remnants of this project's demo.
        & (Join-Path $PSScriptRoot 'stop_demo.ps1') -Quiet
    }
    $occupied = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners() |
        Where-Object { $_.Port -eq $proxyPort }
    if ($occupied) { throw '8767端口已占用；未操作不属于当前演示的进程。' }

    $python = Join-Path $projectRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) { throw '未找到项目 Python 环境，请先完成本机安装。' }
    $ssh = (Get-Command ssh.exe -ErrorAction Stop).Source
    if (-not (Test-Path -LiteralPath $sshConfig)) {
        [IO.File]::WriteAllText($sshConfig, '# Isolated demo SSH config; no user identities are loaded.' + [Environment]::NewLine, $utf8)
    }
    if (-not (Test-Path -LiteralPath $knownHosts) -or (Get-Item -LiteralPath $knownHosts).Length -eq 0) {
        $scanner = Join-Path (Split-Path -Parent $ssh) 'ssh-keyscan.exe'
        $scanLog = Join-Path $outputDir 'share-keyscan.stderr.log'
        $scanned = & $scanner -T 10 -p 22 localhost.run 2> $scanLog
        $keys = @($scanned | Where-Object { $_ -match '^localhost\.run\s+(?:ssh-|ecdsa-)' })
        if (-not $keys.Count) { throw '无法取得隧道主机公钥；未启动公网连接。' }
        # First connection uses TOFU; subsequent connections require these keys.
        [IO.File]::WriteAllText($knownHosts, ($keys -join [Environment]::NewLine) + [Environment]::NewLine, $utf8)
    }

    $access.public_origin = ''
    Write-JsonAtomic $configPath $access
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'
    $proxyArguments = '"{0}" --config "{1}" --port {2}' -f $proxyScript, $configPath, $proxyPort
    $proxy = Start-Process -FilePath $python -ArgumentList $proxyArguments -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $outputDir 'share-proxy.stdout.log') `
        -RedirectStandardError (Join-Path $outputDir 'share-proxy.stderr.log') -PassThru
    $startedInThisRun = $true
    $state = [ordered]@{proxy_pid=$proxy.Id;tunnel_pid=$null;public_url='';started_at=(Get-Date).ToUniversalTime().ToString('o');updated_at=(Get-Date).ToUniversalTime().ToString('o')}
    Write-JsonAtomic $statePath $state
    $proxyReady = $false
    $deadline = (Get-Date).AddSeconds(20)
    do {
        if ((Get-HttpStatus ('http://127.0.0.1:' + $proxyPort + '/')) -eq 401) { $proxyReady = $true; break }
        if (-not (Get-OwnedProcess $proxy.Id 'proxy')) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    if (-not $proxyReady) { throw '访问保护代理未就绪；请查看 output/share-proxy.stderr.log。' }
    # Windows venv python.exe can be a launcher. Record the actual, independently
    # verified Python child when present, rather than assuming the launcher is
    # the listening server process.
    $children = @(Get-CimInstance Win32_Process -Filter ('ParentProcessId = ' + $proxy.Id) -ErrorAction SilentlyContinue)
    foreach ($child in $children) {
        if (Get-OwnedProcess $child.ProcessId 'proxy') {
            $state.proxy_pid = [int]$child.ProcessId
            Write-JsonAtomic $statePath $state
            break
        }
    }

    $tunnelArguments = '-F "{0}" -o IdentityAgent=none -o IdentityFile=none -o PubkeyAuthentication=no -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o StrictHostKeyChecking=yes -o "UserKnownHostsFile={1}" -o ServerAliveInterval=60 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes -o ConnectTimeout=15 -T -R 80:127.0.0.1:{2} nokey@localhost.run -- --output json --inject-http-proxy-headers' -f $sshConfig, $knownHosts, $proxyPort
    $tunnel = Start-Process -FilePath $ssh -ArgumentList $tunnelArguments -WorkingDirectory $projectRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $outputDir 'share-tunnel.stdout.log') `
        -RedirectStandardError (Join-Path $outputDir 'share-tunnel.stderr.log') -PassThru
    $state.tunnel_pid = $tunnel.Id
    $state.updated_at = (Get-Date).ToUniversalTime().ToString('o')
    Write-JsonAtomic $statePath $state

    $publicUrl = ''
    $deadline = (Get-Date).AddSeconds(45)
    do {
        $logPath = Join-Path $outputDir 'share-tunnel.stdout.log'
        if (Test-Path -LiteralPath $logPath) { $publicUrl = Get-PublicUrl (Read-RunningLog $logPath) }
        if ($publicUrl) { break }
        if (-not (Get-OwnedProcess $tunnel.Id 'tunnel')) { break }
        Start-Sleep -Milliseconds 500
    } while ((Get-Date) -lt $deadline)
    if (-not $publicUrl) { throw '未取得演示网址；请查看 output/share-tunnel.stderr.log。' }
    $access.public_origin = $publicUrl
    Write-JsonAtomic $configPath $access
    $state.public_url = $publicUrl
    $state.updated_at = (Get-Date).ToUniversalTime().ToString('o')
    Write-JsonAtomic $statePath $state
    Ensure-Watcher $state
    Write-Instructions $publicUrl $access
    Show-Access $publicUrl
} catch {
    if ($startedInThisRun) {
        try { & (Join-Path $PSScriptRoot 'stop_demo.ps1') -Quiet } catch {}
    }
    Write-Error ('演示启动失败：' + $_.Exception.Message)
    exit 1
}
