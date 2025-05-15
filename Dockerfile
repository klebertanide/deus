FROM python:3.10-slim

# 1) Instala dependências de SO para ffmpeg
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg libsm6 libxext6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2) Instala as deps Python
COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 3) Copia o código
COPY . .

# 4) Variável de ambiente e porta
ENV PORT=5000
EXPOSE 5000

# 5) Comando de inicialização
CMD ["python", "main.py"]
