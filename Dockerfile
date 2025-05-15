FROM python:3.10-slim

# Instala dependências de SO para o MoviePy
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) Copia o requirements e instala as libs Python
COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 2) Copia todo o código da sua API
COPY . .

# 3) Expõe a porta e inicia o app
ENV PORT=5000
EXPOSE 5000
CMD ["python", "main.py"]