FROM python:3.10-slim

# Instala ffmpeg e libs obrigatórias pro moviepy
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Copia todos os arquivos para a raiz do container
COPY . .

# Instala as dependências do projeto
RUN pip install --no-cache-dir -r requirements.txt

# Define porta para Flask
ENV PORT=5000
EXPOSE 5000

# Executa o script principal
CMD ["python", "main.py"]