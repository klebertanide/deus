# Usa imagem base com Python 3.11
FROM python:3.11-slim

WORKDIR /app

# Copia o projeto e o .well-known
COPY . /app
COPY .well-known /app/.well-known

# Dependências de SO para vídeo e imagens
RUN apt-get update && apt-get install -y \
    ffmpeg libsm6 libxext6 git \
  && rm -rf /var/lib/apt/lists/*

# Instala PyTorch CPU-only compatível com Python 3.11
RUN pip install --no-cache-dir \
    https://download.pytorch.org/whl/cpu/torch-2.2.2%2Bcpu-cp311-cp311-linux_x86_64.whl \
    torchaudio==2.0.2

# Instala o restante das libs
RUN pip install --no-cache-dir -r requirements.txt

# Porta do Flask
EXPOSE 5000

CMD ["python", "main.py"]
