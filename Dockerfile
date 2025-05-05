# ── Dockerfile ───────────────────────────────────────────────
FROM python:3.11-slim

# Instala dependências de sistema + Google Chrome
RUN apt-get update -qq && \
    apt-get install -y -qq wget gnupg ca-certificates fonts-liberation && \
    wget -q -O - https://dl.google.com/linux/linux_signing_key.pub | apt-key add - && \
    echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" \
        > /etc/apt/sources.list.d/google.list && \
    apt-get update -qq && \
    apt-get install -y -qq google-chrome-stable && \
    rm -rf /var/lib/apt/lists/*

# ---- Python libs ----
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o projeto
COPY . .

ENV PORT=3000
CMD ["python", "main.py"]
# ─────────────────────────────────────────────────────────────
