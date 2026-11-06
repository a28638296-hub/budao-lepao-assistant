param([string]$Python = "python")
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
& $Python -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "Build dependency installation failed" }
& $Python -m PyInstaller --noconfirm --clean --onefile --windowed --name LePaoRunner --paths src --distpath dist --workpath build --specpath build launch.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
Write-Output "Built dist/LePaoRunner.exe"
