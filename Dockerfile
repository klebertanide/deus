FROM python:3.10-slim

# 1) SO-deps para MoviePy/ffmpeg
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      libsm6 \
      libxext6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2) Instala só as libs Python
COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 3) Copia o código
COPY . .

# 4) Expõe porta e inicia
ENV PORT=5000
EXPOSE 5000
CMD ["python", "main.py"]