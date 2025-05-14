# 1) Base
FROM python:3.10-slim

# 2) SO dependencies para MoviePy
RUN apt-get update && apt-get install -y \
    ffmpeg libsm6 libxext6 \
  && rm -rf /var/lib/apt/lists/*

# 3) Define o workdir (todo o seu código ficará em /app dentro do container)
WORKDIR /app

# 4) Copia apenas o requirements e instala
COPY requirements.txt ./

# Este passo deve aparecer ANTES de copiar o seu código.
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 && pip show moviepy        # <-- deve imprimir Name: moviepy  Version: 2.1.2

# 5) Agora copia todo o restante do seu código
COPY . .

# 6) Porta e startup
ENV PORT=5000
EXPOSE 5000
CMD ["python", "main.py"]
