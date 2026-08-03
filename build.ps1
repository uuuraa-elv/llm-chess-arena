# Build LLM Chess Arena ke .exe (jalankan dari folder project).
#   .\build.ps1            -> onedir  (folder dist\LLMChessArena\, start cepat)
#   .\build.ps1 -OneFile   -> single  (dist\LLMChessArena-portable.exe, 1 file)
param([switch]$OneFile)

$py = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $py)) { $py = "python" }   # fallback ke Python global

$common = @(
    "-m", "PyInstaller", "--noconfirm", "--windowed",
    "--add-data", "app/ui/web;app/ui/web", "main.py"
)

if ($OneFile) {
    & $py @common --onefile --name "LLMChessArena-portable"
} else {
    & $py @common --name "LLMChessArena"
}
