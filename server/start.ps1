# duo_server を起動する。start.bat から呼ばれる。
# 初回は、準備（Pythonの仮想環境と、必要なパッケージ）もここで行う。
# Python や ffmpeg が見つからないときは、winget で入れるかを聞く。

$ErrorActionPreference = 'Continue'
Set-Location -LiteralPath $PSScriptRoot

function Stop-WithMessage([string]$message) {
    Write-Host ""
    Write-Host $message -ForegroundColor Red
    Write-Host ""
    Read-Host "Enter キーで閉じます" | Out-Null
    exit 1
}

function Confirm-Yes([string]$question) {
    $answer = Read-Host "$question [Y/N]"
    return $answer -match '^[Yy]'
}

# winget で入れたあと、PATH の変更を、この画面にも反映する
function Update-PathFromSystem {
    $machine = [Environment]::GetEnvironmentVariable('Path', 'Machine')
    $user = [Environment]::GetEnvironmentVariable('Path', 'User')
    $env:Path = "$machine;$user"
}

function Install-WithWinget([string]$id, [string]$name) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        Stop-WithMessage "$name が見つかりません。winget もないため、自分で $name を入れてから、もう一度起動してください。"
    }
    if (-not (Confirm-Yes "$name が見つかりません。winget で入れますか？")) {
        Stop-WithMessage "$name を入れてから、もう一度起動してください。"
    }
    winget install --id $id -e --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "$name を入れられませんでした。上のメッセージを確認してください。"
    }
    Update-PathFromSystem
}

# Python 3.10 以上を探す。見つかったら、起動のしかた（コマンドと引数）を返す
function Find-Python {
    $candidates = @(
        @{ Command = 'py'; Args = @('-3') },
        @{ Command = 'python'; Args = @() }
    )
    foreach ($c in $candidates) {
        if (-not (Get-Command $c.Command -ErrorAction SilentlyContinue)) { continue }
        # Microsoft Store の案内だけを出す python もあるので、実際に動かして版を確かめる
        & $c.Command @($c.Args) -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return $c }
    }
    return $null
}

Write-Host "duo_server の準備をしています…"

# 1. Python
$python = Find-Python
if (-not $python) {
    Install-WithWinget 'Python.Python.3.12' 'Python 3.10 以上'
    $python = Find-Python
    if (-not $python) {
        Stop-WithMessage "Python を入れましたが、見つけられませんでした。この画面を閉じて、もう一度 start.bat を起動してください。"
    }
}

# 2. ffmpeg（画面を撮って、動画にするのに使う）
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Install-WithWinget 'Gyan.FFmpeg' 'ffmpeg'
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
        Stop-WithMessage "ffmpeg を入れましたが、見つけられませんでした。この画面を閉じて、もう一度 start.bat を起動してください。"
    }
}

# 3. 仮想環境（.venv）。このフォルダの中だけに、必要なパッケージを入れる
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Host "Python の仮想環境を作っています…"
    & $python.Command @($python.Args) -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $venvPython)) {
        Stop-WithMessage "仮想環境を作れませんでした。上のメッセージを確認してください。"
    }
}

# 4. パッケージ。requirements.txt が変わったときだけ入れ直す
$requirements = Join-Path $PSScriptRoot 'requirements.txt'
$stamp = Join-Path $PSScriptRoot '.venv\requirements.sha256'
$hash = (Get-FileHash -LiteralPath $requirements -Algorithm SHA256).Hash
$installed = if (Test-Path -LiteralPath $stamp) { (Get-Content -LiteralPath $stamp -Raw).Trim() } else { '' }
if ($installed -ne $hash) {
    Write-Host "必要なパッケージを入れています…"
    & $venvPython -m pip install --disable-pip-version-check -q -r $requirements
    if ($LASTEXITCODE -ne 0) {
        Stop-WithMessage "パッケージを入れられませんでした。インターネットにつながっているか確認してください。"
    }
    Set-Content -LiteralPath $stamp -Value $hash
}

# 5. 起動。start.bat に付けた引数（--fps 30 など）は、そのまま渡す
Write-Host ""
& $venvPython -m duo_server @args
$code = $LASTEXITCODE

Write-Host ""
if ($code -ne 0) {
    Write-Host "サーバーが止まりました（コード $code）。上のメッセージを確認してください。" -ForegroundColor Red
} else {
    Write-Host "サーバーを終了しました。"
}
Read-Host "Enter キーで閉じます" | Out-Null
