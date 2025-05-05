#!/bin/bash
echo "▶ Instalando Playwright + Chromium"
pip install --upgrade pip
pip install -r requirements.txt
playwright install chromium
