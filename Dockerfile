# Dockerfile (na raiz do repo)
FROM python:3.10-slim

# 1) Instala dependências do sistema para MoviePy
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
  && rm -rf /var/lib/apt/lists/*

# 2) Define diretório de trabalho
WORKDIR /app

# 3) Copia e instala requirements
COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 4) Debug: mostra se o moviepy e demais libs realmente estão presentes
RUN echo "=== DEPENDÊNCIAS INSTALADAS ===" \
 && pip show moviepy numpy imageio imageio-ffmpeg

# 5) Debug: checa import via um script Python
RUN python - <<EOF
import moviepy.editor as m
import numpy as np
import imageio
import imageio_ffmpeg
print("MOVIEPY OK ->", m.__version__)
print("NUMPY  OK ->", np.__version__)
print("IMAGEIO OK ->", imageio.__version__, imageio_ffmpeg.__version__)
EOF

# 6) Copia o restante da sua aplicação
COPY . .

# 7) Porta e comando de inicialização
ENV PORT=5000
EXPOSE 5000
CMD ["python", "main.py"]
