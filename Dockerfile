# Usa uma imagem base mais enxuta
FROM python:3.11-slim

# Define diretório de trabalho
WORKDIR /app

# Copia todos os arquivos do projeto (incluindo .well-known)
COPY . /app

# Instala dependências de sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Instala torch e torchaudio CPU-only, versões compatíveis
RUN pip install --no-cache-dir \
    https://download.pytorch.org/whl/cpu/torch-2.2.2%2Bcpu-cp311-cp311-linux_x86_64.whl \
    https://download.pytorch.org/whl/cpu/torchaudio-2.2.2%2Bcpu-cp311-cp311-linux_x86_64.whl

# Instala o restante das dependências
RUN pip install --no-cache-dir -r requirements.txt

# Expõe porta do Flask
EXPOSE 5000

# Comando padrão
CMD ["python", "main.py"]
