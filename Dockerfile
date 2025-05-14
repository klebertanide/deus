FROM python:3.10-slim

# 1) SO‐dependencies para MoviePy
RUN apt-get update && apt-get install -y \
    ffmpeg libsm6 libxext6 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2) Copia + instala requirements
COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 3) Debug: confirma instalação e importa
RUN echo "=== DEPENDÊNCIAS INSTALADAS ===" \
 && pip show moviepy numpy imageio imageio-ffmpeg \
 && echo "=== TESTE DE IMPORTAÇÃO ===" \
 && python - <<EOF
import moviepy.editor as m
import numpy as np
import imageio
import imageio_ffmpeg
print("MOVIEPY OK →", m.__version__)
print("NUMPY  OK →", np.__version__)
print("IMAGEIO OK →", imageio.__version__, imageio_ffmpeg.__version__)
EOF

# 4) Copia o resto do código
COPY . .

ENV PORT=5000
EXPOSE 5000
CMD ["python", "main.py"]
