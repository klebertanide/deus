# syntax=docker/dockerfile:1
FROM python:3.10-slim

# 1) Dependências de SO para ffmpeg e MoviePy
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      libsm6 \
      libxext6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2) Copia só as deps e instala
COPY requirements.txt ./

# usa python3 -m pip para garantir coerência
RUN python3 -m pip install --upgrade pip \
 && python3 -m pip install --no-cache-dir -r requirements.txt

# 3) Validação de import: falha o build aqui se moviepy/editor não estiver disponível
RUN python3 - << 'EOF'
import moviepy.editor as m
import numpy as np
import imageio, imageio_ffmpeg
print("✔️ Imports ok — MoviePy", m.__version__)
EOF

# 4) Agora copia todo o seu código
COPY . .

EXPOSE 5000
# sempre use python3 
CMD ["python3", "main.py"]
