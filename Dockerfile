FROM python:3.10-slim

# Instala ffmpeg e libs necessárias ao moviepy
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Define a raiz do container como diretório de trabalho
WORKDIR /app

# Copia os arquivos do repositório para dentro do container
COPY . .

# Instala as dependências listadas no requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=5000
EXPOSE 5000

# Executa seu script principal
CMD ["python", "main.py"]