FROM python:3.10-slim

# Instala ffmpeg e dependências para moviepy funcionar
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Copia tudo para a raiz do container
COPY . .

# Instala as dependências
RUN pip install --no-cache