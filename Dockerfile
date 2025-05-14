FROM python:3.10-slim

# instalar dependências de sistema
RUN apt-get update && apt-get install -y \
    ffmpeg libsm6 libxext6 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1) copia só o requirements e instala
COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 && pip show moviepy    # <— aqui a gente confere no log se o moviepy está lá

# 2) copia o resto do código
COPY . .

ENV PORT=5000
EXPOSE 5000

CMD ["python", "main.py"]
