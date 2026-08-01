# start-fitgap.ps1 — one-shot session setup for the fitgap CLI.
#
# Usage:  .\start-fitgap.ps1
#
# What it does:
#   1. Creates the .venv and installs fitgap on first run
#   2. Activates the venv for THIS PowerShell session
#   3. Ensures fitgap.yaml / redact.yaml exist (copied from the examples)
#   4. Checks ANTHROPIC_API_KEY (prompts for it if missing)
#   5. Leaves you ready to run:  fitgap run -t Transcript.txt
#
# If PowerShell refuses to run this script, run once:
#   Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Step($msg)  { Write-Host "  [*] $msg" }
function Ok($msg)    { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Fail($msg)  { Write-Host "  [X] $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "=== fitgap session setup ===" -ForegroundColor Cyan

# --- 1. Virtual environment -------------------------------------------------
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Step "No .venv found - creating it (first run only, takes a minute)..."
    python -m venv .venv
    if (-not (Test-Path $venvPython)) { Fail "venv creation failed - is Python 3.12+ installed?"; return }
    & $venvPython -m pip install --quiet -e ".[dev]"
    Ok "Virtual environment created and fitgap installed."
} else {
    Ok "Virtual environment found."
}

# Activate for this session (PATH + VIRTUAL_ENV persist after the script ends)
$scriptsDir = Join-Path $root ".venv\Scripts"
if (($env:PATH -split ';') -notcontains $scriptsDir) {
    $env:PATH = "$scriptsDir;$env:PATH"
}
$env:VIRTUAL_ENV = Join-Path $root ".venv"

# --- 2. fitgap CLI healthy? -------------------------------------------------
try {
    $version = & "$scriptsDir\fitgap.exe" version 2>$null
    Ok "$version ready."
} catch {
    Warn "fitgap not installed in the venv - installing..."
    & $venvPython -m pip install --quiet -e ".[dev]"
    Ok ((& "$scriptsDir\fitgap.exe" version) + " ready.")
}

# --- 3. Config files --------------------------------------------------------
if (-not (Test-Path (Join-Path $root "fitgap.yaml"))) {
    Copy-Item (Join-Path $root "fitgap.example.yaml") (Join-Path $root "fitgap.yaml")
    Ok "Created fitgap.yaml from the example (edit it to change model/output paths)."
} else {
    Ok "fitgap.yaml present."
}
$modelLine = Select-String -Path (Join-Path $root "fitgap.yaml") -Pattern '^model:' | Select-Object -First 1
if ($modelLine) { Step ("Using " + $modelLine.Line.Trim()) }

if (-not (Test-Path (Join-Path $root "redact.yaml"))) {
    Copy-Item (Join-Path $root "redact.example.yaml") (Join-Path $root "redact.yaml")
    Warn "Created redact.yaml from the example - EDIT IT with your client's name,"
    Warn "people, and codenames before running against real client material:"
    Warn "    notepad redact.yaml"
} else {
    Ok "redact.yaml present (make sure it lists this engagement's client/people names)."
}

# --- 4. API key -------------------------------------------------------------
if ($env:ANTHROPIC_API_KEY) {
    if ($env:ANTHROPIC_API_KEY -notlike "sk-ant-*") {
        Warn "ANTHROPIC_API_KEY is set but doesn't look like an Anthropic key (expected sk-ant-...)."
    } else {
        Ok "ANTHROPIC_API_KEY is set."
    }
} else {
    Warn "ANTHROPIC_API_KEY is not set."
    $entered = $null
    $canPrompt = -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected
    if ($canPrompt) {
        try {
            $secure = Read-Host "  Paste your Anthropic API key (input hidden, Enter to skip)" -AsSecureString
            $entered = [System.Runtime.InteropServices.Marshal]::PtrToStringAuto(
                [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
        } catch {
            # Prompt unavailable - fall through to the instructions below.
        }
    }
    if ($entered) {
        $env:ANTHROPIC_API_KEY = $entered.Trim()
        Ok "API key set for this session."
        Step 'To make it permanent:  setx ANTHROPIC_API_KEY "sk-ant-..."  (then open a new terminal)'
    } else {
        Fail "No API key - classify/verify/transcript stages will not run."
        Step 'Set it with:  $env:ANTHROPIC_API_KEY = "sk-ant-..."'
        Step "Get a key at: https://console.anthropic.com/settings/keys"
    }
}

# --- 5. Ready ---------------------------------------------------------------
Write-Host ""
Write-Host "=== Ready ===" -ForegroundColor Cyan
Write-Host "  Full pipeline from a transcript:"
Write-Host "      fitgap run -t Transcript.txt" -ForegroundColor White
Write-Host "  Other common commands:"
Write-Host "      fitgap run brd.docx backlog.xlsx -t workshop.vtt"
Write-Host "      fitgap run -t Transcript.txt --skip-verify    (cheaper draft, no Learn citations)"
Write-Host "      fitgap report                                 (regenerate the Excel register)"
Write-Host "  Output lands in: fitgap_workspace\fitgap_register.xlsx"
Write-Host ""
