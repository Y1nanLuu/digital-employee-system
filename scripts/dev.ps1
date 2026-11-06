param([Parameter(Mandatory=$true)][ValidateSet('backend','finance','frontend','ai')][string]$Service)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
if (Test-Path '.env') {
    foreach ($line in Get-Content -Encoding utf8 '.env') {
        if ($line -match '^\s*(DEMO_PASSWORD|FINANCE_KEY|POSTGRES_PASSWORD|AI_MODULE_ENABLED|AI_MODULE_CONFIG)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), 'Process')
        }
    }
}
if ($Service -eq 'frontend') {
    Set-Location frontend
    npm.cmd run dev
} elseif ($Service -eq 'ai') {
    # ai-module 的本地模型服务（端口8010）。需要含 transformers/torch 的 Python，
    # 可用环境变量 AI_PYTHON 指定解释器；缺失时只影响模型通道，适配层会降级为规则提取。
    $aiPython = if ($env:AI_PYTHON) { $env:AI_PYTHON } else { 'python' }
    Set-Location ai-module
    & $aiPython llm_server.py
} else {
    $taskPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
    Set-Location backend
    if ($Service -eq 'finance') { & $taskPython -m uvicorn finance.main:app --host 127.0.0.1 --port 8001 }
    else { & $taskPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 }
}
