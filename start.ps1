# 補助金マッチングツール 起動スクリプト
# FastAPIバックエンドとStreamlitフロントエンドを同時に起動する。
#
# 使い方: PowerShellで次を実行
#   .\start.ps1
#
# 停止: 各ウィンドウで Ctrl+C、またはこのスクリプトを閉じる

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

Write-Host "=== 補助金マッチングツールを起動します ===" -ForegroundColor Cyan

# 1. FastAPIバックエンドを別ウィンドウで起動
Write-Host "バックエンド（FastAPI）を起動中... http://127.0.0.1:8000" -ForegroundColor Green
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "cd '$root'; uvicorn backend.main:app --host 127.0.0.1 --port 8000"
)

# バックエンドの起動を待つ
Write-Host "バックエンドの起動を待機中..." -ForegroundColor Yellow
Start-Sleep -Seconds 4

# 2. Streamlitフロントエンドを起動（このウィンドウで実行）
Write-Host "フロントエンド（Streamlit）を起動中... http://localhost:8501" -ForegroundColor Green
Write-Host "ブラウザが自動で開きます。停止するには Ctrl+C を押してください。" -ForegroundColor Cyan
streamlit run app.py
