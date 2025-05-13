# Usa imagem base com Python 3.11-slim
FROM python:3.11-slim

# Define diretório de trabalho
WORKDIR /app

# Copia código e .well-known
COPY . /app
COPY .well-known /app/.well-known

# Dependências de sistema
RUN apt-get update && apt-get install -y \
    ffmpeg libsm6 libxext6 git \
    && rm -rf /var/lib/apt/lists/*

# Instala PyTorch e torchaudio CPU-only (mesma versão)
RUN pip install --no-cache-dir -f https://download.pytorch.org/whl/cpu/torch_stable.html \
    torch==2.2.2+cpu \
    torchaudio==2.2.2+cpu

# Instala demais dependências
RUN pip install --no-cache-dir -r requirements.txt

# Expõe porta Flask
EXPOSE 5000

# Inicia app
CMD ["python", "main.py"]