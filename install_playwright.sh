#!/usr/bin/env bash
# instala dependências Python do projeto…
pip install -r requirements.txt

# baixa **só** o Chromium que o Playwright usa
python -m playwright install chromium
