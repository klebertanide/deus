# Usa Python 3.11 slim para economizar espaço
FROM python:3.11-slim

# Define diretório de trabalho
WORKDIR /app

# Copia tudo para dentro do container
COPY . /app
COPY .well-known /app/.well-known

# Instala libs do sistema
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    git \
  && rm -rf /var/lib/apt/lists/*

# Instala torch e torchaudio compatíveis
RUN pip install --no-cache-dir \
    torch==2.0.1 \
    torchaudio==2.0.2

# Instala o resto das dependências
RUN pip install --no-cache-dir -r requirements.txt

# Expõe porta do Flask
EXPOSE 5000

# Inicia a aplicação
CMD ["python", "main.py"]