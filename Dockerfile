# Usa uma imagem base com Python
FROM python:3.11-slim

# Define diretório de trabalho
WORKDIR /app

# Copia os arquivos do projeto
COPY . /app
COPY .well-known /app/.well-known

# Instala dependências de sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Instala o PyTorch manualmente com suporte adequado
RUN pip install --no-cache-dir torch>=2.0.0,<2.3.0

# Instala as demais dependências do projeto
RUN pip install --no-cache-dir -r requirements.txt

# Expõe a porta
EXPOSE 5000

# Comando padrão
CMD ["python", "main.py"]