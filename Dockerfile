# Usa imagem base leve com Python 3.11
FROM python:3.11-slim

# Define o diretório de trabalho
WORKDIR /app

# Copia todos os arquivos do projeto
COPY . /app
COPY .well-known /app/.well-known

# Instala dependências de sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    git \
  && rm -rf /var/lib/apt/lists/*

# Instala PyTorch CPU-only compatível com Python 3.11
RUN pip install --no-cache-dir \
    https://download.pytorch.org/whl/cpu/torch-2.3.1%2Bcpu-cp311-cp311-linux_x86_64.whl \
    torchaudio==2.3.1

# Instala o restante das dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Expõe a porta padrão do Flask
EXPOSE 5000

# Inicia a aplicação
CMD ["python", "main.py"]