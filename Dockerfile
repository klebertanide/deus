# 1) Imagem base e dependências de SO para MoviePy
FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    ffmpeg libsm6 libxext6 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 2) Copia o requirements e instala tudo
COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 3) Sanity-check de import: evita usar heredoc (que vinha dando parse error)
RUN python3 -c "\
import moviepy.editor as m, numpy as np, imageio, imageio_ffmpeg; \
print('✅ moviepy', m.__version__, '— numpy', np.__version__, '— imageio', imageio.__version__)"

# 4) Copia o restante do código
COPY . .

ENV PORT=5000
EXPOSE 5000
CMD ["python3", "main.py"]
