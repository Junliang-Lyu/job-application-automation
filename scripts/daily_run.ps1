<# Prepares a checkpointed batch for Codex Computer Use; does not submit applications. #>
[CmdletBinding()]
param(
    [string]$RunDir = "",
    [string]$SearchConfig = "config/batch-search.json",
    [string]$InputPath = "",
    [string]$CardListPath = "",
    [string]$PdfPython = "",
    [Alias('AnalyzeOnly')][switch]$DiscoverOnly,
    [switch]$Headless
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$jobRoot = Split-Path -Parent $PSScriptRoot
Set-Location $jobRoot
if (-not $RunDir) { $RunDir = "tmp/batch-$(Get-Date -Format yyyy-MM-dd-HHmmss)" }
if (-not $PdfPython) {
    $hasReportlab = $false
    try {
        & python -c "import reportlab" 2>$null
        if ($LASTEXITCODE -eq 0) {
            $PdfPython = (Get-Command python).Source
            $hasReportlab = $true
        }
    } catch {}
    if (-not $hasReportlab) {
        $candidate = Join-Path $env:USERPROFILE '.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
        if (Test-Path -LiteralPath $candidate) {
            $PdfPython = $candidate
        }
    }
}
if (-not $PdfPython -or -not (Test-Path -LiteralPath $PdfPython)) { throw 'Pass -PdfPython pointing to an existing Python with ReportLab.' }
$jobArgs = @('-X','utf8','scripts/prepare_batch.py','--run-dir',$RunDir,'--config',$SearchConfig,'--pdf-python',$PdfPython)
if ($InputPath) { $jobArgs += @('--input',$InputPath) }
if ($CardListPath) { $jobArgs += @('--cards',$CardListPath) }
if ($DiscoverOnly) { $jobArgs += '--discover-only' }
& python @jobArgs
if ($LASTEXITCODE -eq 3) {
    Write-Host "Platform discovery needed: use the existing Job Search browser, export job cards, then resume with -CardListPath. Full JD snapshots also work with -InputPath. Search plan: $RunDir/browser-search-plan.json"
    exit 3
}
if ($LASTEXITCODE -ne 0) { throw "Batch preparation failed or contains recorded failures: exit $LASTEXITCODE. Inspect $RunDir." }
Write-Host "Preparation complete: $RunDir. Codex Computer Use must review and submit jobs individually."
