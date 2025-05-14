# 1) Base
FROM python:3.10-slim

# 2) Dependências de SO para MoviePy
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
  && rm -rf /var/lib/apt/lists/*

# 3) Define diretório de trabalho
WORKDIR /app

# 4) Copia só o requirements e instala tudo
COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 5) Agora copia o restante do seu código
COPY . .

# 6) Porta e comando
ENV PORT=5000
EXPOSE 5000
CMD ["python", "main.py"]
