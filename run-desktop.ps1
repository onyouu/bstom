$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11+ is required.' }
    & '.\.venv\Scripts\python.exe' -m pip install -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
$bstomOllama = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $bstomOllama) { throw 'Install Ollama first: https://ollama.com/download/windows' }
try { Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null }
catch { Start-Process $bstomOllama.Source -ArgumentList 'serve' -WindowStyle Hidden }
$bstomReady = $false
for ($bstomAttempt = 0; $bstomAttempt -lt 20; $bstomAttempt++) {
    try {
        Invoke-RestMethod 'http://127.0.0.1:11434/api/version' -TimeoutSec 2 | Out-Null
        $bstomReady = $true
        break
    }
    catch { Start-Sleep -Milliseconds 500 }
}
if (-not $bstomReady) { throw 'Ollama did not start. Open Ollama and try again.' }
& '.\.venv\Scripts\pythonw.exe' start_desktop.py
