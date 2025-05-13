# Escolhe imagem oficial Python 3.11
FROM python:3.11-slim

# Define diretório de trabalho
WORKDIR /app

# Copia todo o projeto
COPY . /app
COPY .well-known /app/.well-known

# Instala dependências de sistema para MoviePy, imageio, etc.
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    git \
  && rm -rf /var/lib/apt/lists/*

# Instala PyTorch CPU e torchaudio compatível
RUN pip install --no-cache-dir \
    torch==2.0.1 \
    torchaudio==2.0.2

# Instala as demais dependências do projeto
RUN pip install --no-cache-dir -r requirements.txt

# Expõe a porta padrão do Flask
EXPOSE 5000

# Comando para iniciar a aplicação
CMD ["python", "main.py"]