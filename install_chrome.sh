#!/usr/bin/env bash
set -e

echo "▶ Instalando Google Chrome..."
apt-get update -qq
apt-get install -y -qq wget gnupg ca-certificates

# chave do repositório Google
wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add -
echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" \
     > /etc/apt/sources.list.d/google.list

apt-get update -qq
apt-get install -y -qq google-chrome-stable
echo "✔ Chrome instalado"
