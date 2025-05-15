# Dockerfile
FROM python:3.10-slim

# 1) Dependências de SO para MoviePy
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      libsm6 \
      libxext6 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2) Copia só o requirements para cachear instalação
COPY requirements.txt ./

# 3) Instala as dependências Python
RUN pip3 install --upgrade pip \
 && pip3 install --no-cache-dir -r requirements.txt

# 4) Debug rápido para garantir que o moviepy entrou no mesmo PYTHONPATH
RUN echo "=== DEBUG: PYTHON PATH E IMPORTS ===" \
 && python3 - <<EOF
import sys
print("Python:", sys.executable)
print("sys.path:", sys.path)
try:
    import moviepy.editor as m
    print("moviepy.editor OK →", m.__version__)
except Exception as e:
    print("moviepy.editor falhou →", e)
EOF

# 5) Agora copia todo o código fonte
COPY . .

ENV PORT=5000
EXPOSE 5000

# 6) Usa python3 explicitamente
CMD ["python3", "main.py"]
