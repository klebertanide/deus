# syntax=docker/dockerfile:1
FROM python:3.10-slim

# 1) Depêndencias SO para ffmpeg/MoviePy
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      libsm6 \
      libxext6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2) Instala deps Python
COPY requirements.txt ./
RUN python3 -m pip install --upgrade pip \
 && python3 -m pip install --no-cache-dir -r requirements.txt

# 3) Valida imports — falha aqui no build se não encontrar moviepy.editor
RUN python3 - << 'EOF'
import moviepy.editor as m
import numpy as np
import imageio, imageio_ffmpeg
print("✔️ Imports OK — MoviePy", m.__version__)
EOF

# 4) Copia o resto do código
COPY . .

EXPOSE 5000
CMD ["python3", "main.py"]
