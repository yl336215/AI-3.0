param(
    [int]$Port = 8030
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$url = "http://127.0.0.1:$Port/"

function Test-Ai3Ready {
    try {
        $page = Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 2
        $api = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/labeling/analysis-cards" -TimeoutSec 2
        return ($page.StatusCode -eq 200 -and $null -ne $api.cards)
    } catch {
        return $false
    }
}

if (Test-Ai3Ready) {
    Write-Host "AI-3.0 已在运行：$url"
    Start-Process $url
    exit 0
}

$exe = Join-Path $projectRoot 'AI3-Audio-Labeling.exe'
if (-not (Test-Path $exe)) {
    $exe = Join-Path $projectRoot 'dist\AI3-Audio-Labeling\AI3-Audio-Labeling.exe'
}

if (Test-Path $exe) {
    $exeDir = Split-Path -Parent $exe
    if (-not (Test-Path (Join-Path $exeDir '_internal') -PathType Container)) {
        throw "安装包不完整：$exeDir 缺少 _internal 文件夹。请重新解压完整的 Windows 构建产物。"
    }
    Write-Host "已检查 Windows 安装包：$exe"
    $command = $exe
    $arguments = @()
    $workingDirectory = $exeDir
} else {
    if (-not (Test-Path (Join-Path $projectRoot 'requirements.txt'))) {
        throw '未找到 Windows EXE 或 requirements.txt。请将启动脚本放在 AI-3.0 项目根目录。'
    }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        $command = $pyLauncher.Source
        $pythonPrefix = @('-3.11')
    } else {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) { throw '未找到 Windows EXE 或 Python 3.11。请安装 Python 3.11，或使用打包好的 EXE。' }
        $command = $python.Source
        $pythonPrefix = @()
    }
    & $command @pythonPrefix -c 'import sys; assert sys.version_info[:2] == (3, 11), "需要 Python 3.11"'
    if ($LASTEXITCODE -ne 0) { throw 'Python 版本检查失败。请安装 Python 3.11。' }
    Push-Location $projectRoot
    try {
        & $command @pythonPrefix -c 'from web.api.main import app; print("AI-3.0 依赖检查通过")'
        if ($LASTEXITCODE -ne 0) {
            throw '缺少 AI-3.0 依赖。请在本目录执行：py -3.11 -m pip install -r requirements.txt'
        }
    } finally {
        Pop-Location
    }
    $arguments = $pythonPrefix + @('-m', 'uvicorn', 'web.api.main:app', '--host', '127.0.0.1', '--port', "$Port")
    $workingDirectory = $projectRoot
}

$env:AI3_PORT = "$Port"
$env:AI3_NO_BROWSER = '1'
$logBase = Join-Path $env:TEMP ("ai3-start-{0}-{1}" -f $PID, (Get-Date -Format 'yyyyMMddHHmmss'))
if ($arguments.Count -gt 0) {
    $process = Start-Process -FilePath $command -ArgumentList $arguments -WorkingDirectory $workingDirectory -PassThru `
        -RedirectStandardOutput "$logBase.out.log" -RedirectStandardError "$logBase.err.log"
} else {
    $process = Start-Process -FilePath $command -WorkingDirectory $workingDirectory -PassThru `
        -RedirectStandardOutput "$logBase.out.log" -RedirectStandardError "$logBase.err.log"
}

for ($attempt = 0; $attempt -lt 120; $attempt++) {
    if (Test-Ai3Ready) {
        Write-Host "AI-3.0 已启动，进程号 $($process.Id)：$url"
        Start-Process $url
        exit 0
    }
    $process.Refresh()
    if ($process.HasExited) { break }
    Start-Sleep -Seconds 1
}

Write-Host "启动日志：$logBase.out.log；$logBase.err.log"
if (Test-Path "$logBase.err.log") { Get-Content "$logBase.err.log" -Tail 30 }
throw "AI-3.0 未能在 $url 启动。"
