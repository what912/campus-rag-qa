$ErrorActionPreference = 'Stop'
$launchArguments = @($args)
$projectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$toolsDirectory = Join-Path $projectRoot '.tools'
$runtimeDirectory = Join-Path $toolsDirectory 'python312'
$runtimePython = Join-Path $runtimeDirectory 'python.exe'
$firstRunScript = Join-Path $projectRoot 'scripts\first_run.py'
$runtimeUrl = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.zip'
$runtimeSha256 = '8649692de846c56a7189d6dae5c322ab20deb1b5908b6f39426b62a36f39415d'
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'

function Find-UsablePython([string]$Command, [string[]]$Prefix = @()) {
    # Isolated mode keeps user site packages and PYTHONPATH out of the probe.
    # JSON ASCII output survives Windows PowerShell 5.1 native stdout decoding
    # even when the extracted project directory contains Chinese characters.
    $probe = 'import sys, struct, venv, ensurepip, json; assert sys.version_info[:2] == (3, 12) and struct.calcsize(''P'') == 8; print(json.dumps(sys.executable, ensure_ascii=True))'
    try {
        $result = @(& $Command @Prefix -I -c $probe 2>$null)
        if ($LASTEXITCODE -eq 0 -and $result.Count -gt 0) {
            $executable = ConvertFrom-Json ([string]$result[-1])
            if (Test-Path -LiteralPath $executable -PathType Leaf) {
                return $executable
            }
        }
    } catch {
        # An incomplete environment is repaired by using another base runtime.
    }
    return $null
}

function Assert-BootstrapPath([string]$Path) {
    $fullPath = [IO.Path]::GetFullPath($Path)
    $toolsPrefix = [IO.Path]::GetFullPath($toolsDirectory).TrimEnd('\') + '\'
    if (-not $fullPath.StartsWith($toolsPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw '运行时临时路径不在本项目的 .tools 目录内，已停止操作。'
    }
    return $fullPath
}

function Remove-BootstrapTemporary([string]$Path) {
    $checkedPath = Assert-BootstrapPath $Path
    if (Test-Path -LiteralPath $checkedPath) {
        Remove-Item -LiteralPath $checkedPath -Force -Recurse
    }
}

function Install-ProjectPython {
    New-Item -ItemType Directory -Path $toolsDirectory -Force | Out-Null
    $lockPath = Join-Path $toolsDirectory 'python-bootstrap.lock'
    $downloadPath = Join-Path $toolsDirectory ('.python312-download-' + $PID + '.zip')
    $stagingPath = Join-Path $toolsDirectory ('.python312-extract-' + $PID)
    $lockHandle = $null
    $waitUntil = [DateTime]::UtcNow.AddMinutes(5)
    $waitMessageShown = $false
    try {
        while ($null -eq $lockHandle) {
            try {
                $lockHandle = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate,
                    [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            } catch [IO.IOException] {
                if ([DateTime]::UtcNow -gt $waitUntil) {
                    throw '另一个启动窗口仍在准备 Python，请等待它完成后重新双击“启动系统.cmd”。'
                }
                if (-not $waitMessageShown) {
                    Write-Host '另一个启动窗口正在准备 Python，正在等待……'
                    $waitMessageShown = $true
                }
                Start-Sleep -Milliseconds 500
            }
        }

        # Another launcher may have completed the download while we waited.
        if (Test-Path -LiteralPath $runtimePython -PathType Leaf) {
            $existingPython = Find-UsablePython $runtimePython
            if ($existingPython) {
                return $existingPython
            }
        }

        Write-Host '首次启动：正在从 Python 官网下载本项目专用的 Python 3.12（约 31 MB）……'
        Write-Host '文件仅保存在项目 .tools 文件夹，不需要管理员权限。'
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -UseBasicParsing -Uri $runtimeUrl -OutFile $downloadPath -TimeoutSec 180 -MaximumRedirection 3
        $hashAlgorithm = [Security.Cryptography.SHA256]::Create()
        $downloadStream = [IO.File]::OpenRead($downloadPath)
        try {
            $actualHash = [BitConverter]::ToString($hashAlgorithm.ComputeHash($downloadStream)).Replace('-', '')
        } finally {
            $downloadStream.Dispose()
            $hashAlgorithm.Dispose()
        }
        if ($actualHash -ne $runtimeSha256) {
            throw 'Python 下载文件的 SHA-256 校验失败，已停止使用该文件。请检查网络后重新双击启动。'
        }

        Write-Host 'Python 下载校验通过，正在解压……'
        Remove-BootstrapTemporary $stagingPath
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($downloadPath, $stagingPath)
        foreach ($requiredFile in @('python.exe', 'Lib\venv\__init__.py', 'Lib\ensurepip\__init__.py', 'Lib\site-packages\pip\__init__.py')) {
            if (-not (Test-Path -LiteralPath (Join-Path $stagingPath $requiredFile) -PathType Leaf)) {
                throw 'Python 运行时文件不完整，请检查网络和磁盘空间后重新双击启动。'
            }
        }
        if (-not (Find-UsablePython (Join-Path $stagingPath 'python.exe'))) {
            throw '本机无法运行下载的 64 位 Python 3.12，请确认使用受支持的 64 位 Windows。'
        }

        if (Test-Path -LiteralPath $runtimeDirectory) {
            $backupPath = Join-Path $toolsDirectory ('python312-incomplete-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss') + '-' + $PID)
            $checkedSource = Assert-BootstrapPath $runtimeDirectory
            $checkedDestination = Assert-BootstrapPath $backupPath
            Move-Item -LiteralPath $checkedSource -Destination $checkedDestination
        }
        $checkedStaging = Assert-BootstrapPath $stagingPath
        $checkedRuntime = Assert-BootstrapPath $runtimeDirectory
        Move-Item -LiteralPath $checkedStaging -Destination $checkedRuntime
        $installedPython = Find-UsablePython $runtimePython
        if (-not $installedPython) {
            throw '项目 Python 准备未完成，请重新双击“启动系统.cmd”重试。'
        }
        return $installedPython
    } finally {
        if ($null -ne $lockHandle) {
            try {
                Remove-BootstrapTemporary $downloadPath
                Remove-BootstrapTemporary $stagingPath
            } finally {
                $lockHandle.Dispose()
            }
        }
    }
}

$startExitCode = 1
try {
    if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
        throw '一键启动需要 64 位 Windows，请在 64 位 Windows 电脑上解压并运行本项目。'
    }
    if (-not (Test-Path -LiteralPath $firstRunScript -PathType Leaf)) {
        throw '缺少 scripts\first_run.py，请先完整解压 GitHub 下载的项目 ZIP，再双击“启动系统.cmd”。'
    }
    Set-Location -LiteralPath $projectRoot
    $selectedPython = $null
    foreach ($candidate in @((Join-Path $projectRoot '.venv\Scripts\python.exe'), $runtimePython)) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            $selectedPython = Find-UsablePython $candidate
            if ($selectedPython) { break }
        }
    }
    if (-not $selectedPython) {
        foreach ($commandName in @('py.exe', 'python.exe')) {
            $commandInfo = Get-Command $commandName -CommandType Application -ErrorAction SilentlyContinue
            if (-not $commandInfo -or $commandInfo.Source -like '*\Microsoft\WindowsApps\*') { continue }
            $prefixArguments = @()
            if ($commandName -eq 'py.exe') { $prefixArguments = @('-3.12') }
            $selectedPython = Find-UsablePython $commandInfo.Source $prefixArguments
            if ($selectedPython) { break }
        }
    }
    if (-not $selectedPython) {
        $selectedPython = Install-ProjectPython
    }
    & $selectedPython $firstRunScript @launchArguments
    $startExitCode = $LASTEXITCODE
} catch {
    Write-Host ''
    Write-Host ('启动未完成：' + $_.Exception.Message) -ForegroundColor Red
    Write-Host '请保持联网、确认项目已完整解压且文件夹可写，然后重新双击“启动系统.cmd”。'
    $startExitCode = 1
}
exit $startExitCode
