param(
    [string]$Target
)

$SafeName = $Target -replace "/", "__"
$RepoDir = "targets/$SafeName"
$LogDir = "logs/$SafeName/windows"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$LogFile = "$LogDir/setup.log"

try {
    Set-Location $RepoDir

    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel

    if (Test-Path requirements.txt) {
        .\.venv\Scripts\python.exe -m pip install -r requirements.txt
    }

    if (Test-Path requirements-dev.txt) {
        .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
    }

    if ((Test-Path pyproject.toml) -or (Test-Path setup.py)) {
        .\.venv\Scripts\python.exe -m pip install -e .
    }
}
catch {
    $_ | Out-File -Append $LogFile
    exit 1
}