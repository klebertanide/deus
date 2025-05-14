# Usa imagem Python com ferramentas básicas
FROM python:3.10-slim

# Instala ffmpeg e dependências do moviepy
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Define diretório de trabalho
WORKDIR /app

# Copia os arquivos do projeto
COPY . .

# Instala as dependências do projeto
RUN pip install --no-cache-dir -r requirements.txt

# Expõe a porta
ENV PORT=5000
EXPOSE 5000

# Comando para iniciar o servidor Flask
CMD ["python", "main.py"]