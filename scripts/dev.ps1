param([Parameter(Mandatory=$true)][ValidateSet('backend','finance','frontend')][string]$Service)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path $PSScriptRoot -Parent
Set-Location $projectRoot
if (Test-Path '.env') {
    foreach ($line in Get-Content -Encoding utf8 '.env') {
        if ($line -match '^\s*(DEMO_PASSWORD|FINANCE_KEY|POSTGRES_PASSWORD)=(.*)$') {
            [Environment]::SetEnvironmentVariable($matches[1], $matches[2].Trim(), 'Process')
        }
    }
}
if ($Service -eq 'frontend') {
    Set-Location frontend
    npm.cmd run dev
} else {
    $taskPython = Join-Path $projectRoot '.venv/Scripts/python.exe'
    Set-Location backend
    if ($Service -eq 'finance') { & $taskPython -m uvicorn finance.main:app --host 127.0.0.1 --port 8001 }
    else { & $taskPython -m uvicorn app.main:app --host 127.0.0.1 --port 8000 }
}
