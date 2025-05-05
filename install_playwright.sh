#!/usr/bin/env bash
set -e
echo "▶ Instalando Playwright + Chromium"
pip install --no-cache-dir --upgrade pip
pip install --no-cache-dir -r requirements.txt
python -m playwright install --with-deps chromium
echo "✔ Playwright pronto"
