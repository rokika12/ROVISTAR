$ErrorActionPreference = "Continue"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonPath = Join-Path $projectRoot ".venv\Scripts\python.exe"
$logDirectory = Join-Path $env:LOCALAPPDATA "ROKIRA"
$logPath = Join-Path $logDirectory "bot.log"

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
Set-Location $projectRoot

while ($true) {
    "[$(Get-Date -Format o)] Starting ROKIRA Telegram bot." | Add-Content -Path $logPath -Encoding UTF8
    try {
        & $pythonPath -u -m bot.main 2>&1 | ForEach-Object {
            "[$(Get-Date -Format o)] $_" | Add-Content -Path $logPath -Encoding UTF8
        }
        $exitCode = $LASTEXITCODE
    } catch {
        $exitCode = 1
        "[$(Get-Date -Format o)] Runner error: $_" | Add-Content -Path $logPath -Encoding UTF8
    }
    "[$(Get-Date -Format o)] Bot exited with code $exitCode; retrying in 60 seconds." | Add-Content -Path $logPath -Encoding UTF8
    Start-Sleep -Seconds 60
}