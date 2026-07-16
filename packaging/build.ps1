# VideoSummary PyInstaller 打包脚本（Windows PowerShell）
# 产物输出到 dist/VideoSummary/

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot\..

Write-Host "=== 0. 清理旧产物 ==="
Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue

Write-Host "=== 1. 预下载 tiktoken BPE ==="
uv run python -c "import tiktoken; tiktoken.get_encoding('cl100k_base'); print('BPE OK')"

Write-Host "=== 2. 确保二进制文件存在 ==="
$binDir = "packaging\bin"
if (-not (Test-Path "$binDir\ffmpeg.exe")) {
    Write-Warning "ffmpeg.exe 未找到！请下载放到 $binDir"
    Write-Warning "https://www.gyan.dev/ffmpeg/builds/ (essentials build)"
}
if (-not (Test-Path "$binDir\yt-dlp.exe")) {
    Write-Warning "yt-dlp.exe 未找到！请从 https://github.com/yt-dlp/yt-dlp/releases 下载放到 $binDir"
}

Write-Host "=== 3. 运行 PyInstaller ==="
uv run pyinstaller --clean packaging\videosummary.spec

Write-Host "=== 4. 复制 yt-dlp 种子到 dist（供首次运行拷贝到用户目录） ==="
if (Test-Path "$binDir\yt-dlp.exe") {
    New-Item -ItemType Directory -Force -Path dist\VideoSummary\bin | Out-Null
    Copy-Item "$binDir\yt-dlp.exe" dist\VideoSummary\bin\
}

Write-Host "=== 5. 验证打包产物 ==="
$exe = "dist\VideoSummary\VideoSummary.exe"
if (Test-Path $exe) {
    $sizeMB = [math]::Round((Get-Item $exe).Length / 1MB, 1)
    Write-Host "  VideoSummary.exe: $sizeMB MB"
} else {
    Write-Error "  VideoSummary.exe 不存在！"
}

$dirSizeMB = [math]::Round((Get-ChildItem dist\VideoSummary -Recurse | Measure-Object Length -Sum).Sum / 1MB, 1)
Write-Host "  总大小: $dirSizeMB MB"

Write-Host "=== 打包完成！产物在 dist\VideoSummary\ ==="
Pop-Location
