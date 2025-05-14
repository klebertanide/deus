FROM python:3.10-slim

# 1) Dependências de sistema para MoviePy
RUN apt-get update && apt-get install -y \
    ffmpeg libsm6 libxext6 \
  && rm -rf /var/lib/apt/lists/*

# 2) Diretório de trabalho
WORKDIR /app

# 3) Instala requirements e faz debug de instalação/importação
COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    echo "=== DEPENDÊNCIAS INSTALADAS ===" && \
    pip show moviepy numpy imageio imageio-ffmpeg && \
    echo "=== TESTE DE IMPORTAÇÃO ===" && \
    python - <<EOF
import moviepy.editor as m
import numpy as np
import imageio
import imageio_ffmpeg
print("MOVIEPY OK, versão:", m.__version__)
print("NUMPY OK, versão:", np.__version__)
print("IMAGEIO OK, versões:", imageio.__version__, imageio_ffmpeg.__version__)
EOF

# 4) Copia o código da aplicação
COPY . .

# 5) Porta e comando de inicialização
ENV PORT=5000
EXPOSE 5000
CMD ["python", "main.py"]
