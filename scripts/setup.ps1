$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (!(Test-Path '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python环境创建失败' }
}
& ./.venv/Scripts/python.exe -m pip install -r backend/requirements.txt --index-url https://pypi.org/simple
if ($LASTEXITCODE -ne 0) { throw '后端依赖安装失败' }
Push-Location frontend
try {
    npm.cmd ci --cache ../work/npm-cache --registry https://registry.npmjs.org
    if ($LASTEXITCODE -ne 0) { throw '前端依赖安装失败' }
} finally { Pop-Location }
Write-Host '依赖准备完成，启动方式请查看README。'
