FROM python:3.10-slim

# Instala dependências de sistema para MoviePy
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
  && rm -rf /var/lib/apt/lists/*

# Define diretório de trabalho
WORKDIR /app

# 1) Copia só o requirements e instala
COPY requirements.txt ./
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# 2) Agora copia o restante do código
COPY . .

ENV PORT=5000
EXPOSE 5000

# Comando de inicialização
CMD ["python", "main.py"]
