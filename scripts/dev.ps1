param([Parameter(Mandatory=$true)][ValidateSet('backend','finance','frontend','ai')][string]$Service)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot

# ASCII-only comments on purpose: Windows PowerShell 5.1 reads BOM-less files with
# the system ANSI code page, so non-ASCII comments can break parsing. Keep this
# launcher ASCII; put Chinese explanation in .env.example and docs.
#
# Load allowed variables from .env (gitignored). A value already set in the current
# terminal wins: .env never overwrites it.
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

# Convenience: if dsapi.txt exists in the project root and DEEPSEEK_API_KEY is unset,
# use it as the key. The file is gitignored; the key still never enters config files.
$keyFile = Join-Path $projectRoot 'dsapi.txt'
if (-not $env:DEEPSEEK_API_KEY -and (Test-Path $keyFile)) {
    $env:DEEPSEEK_API_KEY = (Get-Content $keyFile -Raw).Trim()
}

if ($Service -eq 'frontend') {
    Set-Location frontend
    npm.cmd run dev
} elseif ($Service -eq 'ai') {
    # Local model server on port 8010. Needs a Python with transformers/torch;
    # set AI_PYTHON to point at it. If missing, only the model channel is affected
    # and the adapter falls back to rule extraction.
    $aiPython = if ($env:AI_PYTHON) { $env:AI_PYTHON } else { 'python' }
    Set-Location ai-module
    & $aiPython llm_server.py
} else {
    $taskPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
    Set-Location backend
    if ($Service -eq 'finance') { & $taskPython -m uvicorn finance.main:app --host 127.0.0.1 --port 8001 }
    else { & $taskPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 }
}
