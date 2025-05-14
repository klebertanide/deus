FROM python:3.10-slim

# Instala o ffmpeg e dependências para moviepy funcionar
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Define diretório do projeto
WORKDIR /app

# Copia os arquivos do projeto para dentro do container
COPY . .

# Instala o pip atualizado e todas as dependências
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=5000
EXPOSE 5000

# Roda o script app.py explicitamente
CMD ["python3", "app.py"]
