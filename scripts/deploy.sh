#!/usr/bin/env bash
# Deploy / cập nhật bản mới nhất của main tại /opt/my-gemini-api trên VPS Ubuntu.
# Chạy bằng: bash /opt/my-gemini-api/scripts/deploy.sh
set -euo pipefail

cd /opt/my-gemini-api

echo "==> 1. Lấy code mới từ GitHub"
git fetch --prune origin main
git reset --hard origin/main      # giữ nguyên .env và cookies.json vì đã nằm trong .gitignore

echo "==> 2. Cập nhật thư viện Python trong .venv"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -e . -q

echo "==> 3. Khởi động lại service trên PM2"
if pm2 describe my-gemini-api >/dev/null 2>&1; then
  pm2 restart my-gemini-api --update-env
else
  pm2 start .venv/bin/python --name my-gemini-api -- cli.py serve
fi
pm2 save

echo "==> Xong: $(git log -1 --format='%h %s')"
