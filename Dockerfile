# Usa imagem base Python 3.11 Slim
FROM python:3.11-slim

WORKDIR /app

COPY . /app
COPY .well-known /app/.well-known

RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Instala Torch CPU-only + Torchaudio compatível
RUN pip install --no-cache-dir \
    https://download.pytorch.org/whl/cpu/torch-2.3.1%2Bcpu-cp311-cp311-linux_x86_64.whl \
    torchaudio==2.3.1

# Instala o restante das dependências
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 5000

CMD ["python", "main.py"]
