$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$protectedNames = @(
    "codex-skill",
    "data",
    "review_outputs",
    "src",
    "tests"
)

$safeExactNames = @(
    ".pytest_cache",
    ".tmp",
    "pytest-tmp-clean",
    "skill-smoke"
)

$safeRegexes = @(
    '^tmp-.+'
)

$adminExactNames = @(
    ".basetemp",
    "basetemp_plain"
)

$adminRegexes = @(
    '^pytest-cache-files-.+'
)

function Test-MatchAnyRegex {
    param(
        [string]$Name,
        [string[]]$Regexes
    )

    foreach ($regex in $Regexes) {
        if ($Name -match $regex) {
            return $true
        }
    }
    return $false
}

function Get-ProjectDirectories {
    Get-ChildItem -LiteralPath $projectRoot -Force -Directory
}

function Get-SafeCleanupTargets {
    Get-ProjectDirectories | Where-Object {
        ($protectedNames -notcontains $_.Name) -and (
            ($safeExactNames -contains $_.Name) -or (Test-MatchAnyRegex -Name $_.Name -Regexes $safeRegexes)
        )
    }
}

function Get-AdminCleanupTargets {
    Get-ProjectDirectories | Where-Object {
        ($protectedNames -notcontains $_.Name) -and (
            ($adminExactNames -contains $_.Name) -or (Test-MatchAnyRegex -Name $_.Name -Regexes $adminRegexes)
        )
    }
}

$removed = New-Object System.Collections.Generic.List[string]
$failed = New-Object System.Collections.Generic.List[string]

Write-Host ""
Write-Host "Normal cleanup mode: removing safe temp folders" -ForegroundColor Cyan
Write-Host "Project root: $projectRoot"
Write-Host ""

$targets = @(Get-SafeCleanupTargets)
if (-not $targets) {
    Write-Host "No safe temp folders found." -ForegroundColor Yellow
} else {
    foreach ($target in $targets) {
        try {
            Remove-Item -LiteralPath $target.FullName -Recurse -Force -ErrorAction Stop
            $removed.Add($target.Name) | Out-Null
            Write-Host "[removed] $($target.Name)" -ForegroundColor Green
        }
        catch {
            $failed.Add("$($target.Name) -> $($_.Exception.Message)") | Out-Null
            Write-Host "[failed] $($target.Name) : $($_.Exception.Message)" -ForegroundColor Red
        }
    }
}

$adminTargets = @(Get-AdminCleanupTargets)

Write-Host ""
Write-Host "Cleanup summary" -ForegroundColor Cyan
Write-Host "Removed count: $($removed.Count)"
if ($removed.Count -gt 0) {
    $removed | ForEach-Object { Write-Host "  - $_" }
}

if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "Failed in normal mode:" -ForegroundColor Yellow
    $failed | ForEach-Object { Write-Host "  - $_" }
}

Write-Host ""
if ($adminTargets.Count -gt 0) {
    Write-Host "Run the admin cleanup script for these folders:" -ForegroundColor Yellow
    $adminTargets | ForEach-Object { Write-Host "  - $($_.Name)" }
} else {
    Write-Host "No admin-only temp folders found." -ForegroundColor Green
}

Write-Host ""
Write-Host "Protected folders: codex-skill / data / review_outputs / src / tests" -ForegroundColor DarkGray
