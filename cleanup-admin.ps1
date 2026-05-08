param(
    [switch]$NoElevate
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path

$protectedNames = @(
    "codex-skill",
    "data",
    "review_outputs",
    "src",
    "tests"
)

$cleanupExactNames = @(
    ".basetemp",
    ".pytest_cache",
    ".tmp",
    "basetemp_plain",
    "pytest-tmp-clean",
    "skill-smoke"
)

$cleanupRegexes = @(
    '^pytest-cache-files-.+',
    '^tmp-.+'
)

function Test-IsAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

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

function Get-CleanupTargets {
    Get-ChildItem -LiteralPath $projectRoot -Force -Directory | Where-Object {
        ($protectedNames -notcontains $_.Name) -and (
            ($cleanupExactNames -contains $_.Name) -or (Test-MatchAnyRegex -Name $_.Name -Regexes $cleanupRegexes)
        )
    }
}

function Ensure-Elevated {
    if ($NoElevate) {
        return
    }

    if (Test-IsAdmin) {
        return
    }

    Write-Host "Not elevated. Trying to relaunch as administrator..." -ForegroundColor Yellow
    $argsList = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$PSCommandPath`""
    )
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argsList | Out-Null
    exit
}

function Invoke-ForceRemoveDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path)) {
        return $true
    }

    try {
        Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
        return -not (Test-Path -LiteralPath $Path)
    }
    catch {
    }

    & takeown.exe /F $Path /R /D Y | Out-Null
    & icacls.exe $Path '/inheritance:e' '/grant:r' "$($env:USERNAME):(OI)(CI)F" '/T' '/C' | Out-Null
    cmd /c "attrib -r -s -h `"$Path`" /s /d" | Out-Null
    cmd /c "rd /s /q `"$Path`"" | Out-Null
    return -not (Test-Path -LiteralPath $Path)
}

Ensure-Elevated

$removed = New-Object System.Collections.Generic.List[string]
$failed = New-Object System.Collections.Generic.List[string]

Write-Host ""
Write-Host "Admin cleanup mode: removing temp folders" -ForegroundColor Cyan
Write-Host "Project root: $projectRoot"
Write-Host ""

$targets = @(Get-CleanupTargets)
if (-not $targets) {
    Write-Host "No cleanup targets found." -ForegroundColor Yellow
    exit 0
}

foreach ($target in $targets) {
    $ok = Invoke-ForceRemoveDirectory -Path $target.FullName
    if ($ok) {
        $removed.Add($target.Name) | Out-Null
        Write-Host "[removed] $($target.Name)" -ForegroundColor Green
    } else {
        $failed.Add($target.Name) | Out-Null
        Write-Host "[still present] $($target.Name)" -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "Cleanup summary" -ForegroundColor Cyan
Write-Host "Removed count: $($removed.Count)"
if ($removed.Count -gt 0) {
    $removed | ForEach-Object { Write-Host "  - $_" }
}

if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Host "These folders still could not be removed:" -ForegroundColor Yellow
    $failed | ForEach-Object { Write-Host "  - $_" }
}

Write-Host ""
Write-Host "Protected folders: codex-skill / data / review_outputs / src / tests" -ForegroundColor DarkGray
