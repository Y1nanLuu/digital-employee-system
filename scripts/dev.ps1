param([Parameter(Mandatory=$true)][ValidateSet('backend','finance','frontend','ai')][string]$Service)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
# 允许从 .env 注入的变量（.env 已被 .gitignore 忽略）：
#   - 演示账号与工具密钥
#   - 模型通道：开关、配置路径、以及远程通道密钥（密钥只走环境变量，不写入配置文件）
# 已在当前终端设置的同名环境变量优先：若 .env 里没有该行，就不覆盖。
if (Test-Path '.env') {
    foreach ($line in Get-Content -Encoding utf8 '.env') {
        if ($line -match '^\s*(DEMO_PASSWORD|FINANCE_KEY|POSTGRES_PASSWORD|AI_MODULE_ENABLED|AI_MODULE_CONFIG|DEEPSEEK_API_KEY|GLM_API_KEY|AI_PYTHON)=(.*)$') {
            $name = $matches[1]
            $value = $matches[2].Trim()
            if ($value -and -not [Environment]::GetEnvironmentVariable($name, 'Process')) {
                [Environment]::SetEnvironmentVariable($name, $value, 'Process')
            }
        }
    }
}
# 便捷方式：项目根目录存在 dsapi.txt 且未设置 DEEPSEEK_API_KEY 时，自动作为 DeepSeek 密钥使用
$keyFile = Join-Path $projectRoot 'dsapi.txt'
if (-not $env:DEEPSEEK_API_KEY -and (Test-Path $keyFile)) {
    $env:DEEPSEEK_API_KEY = (Get-Content $keyFile -Raw).Trim()
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
