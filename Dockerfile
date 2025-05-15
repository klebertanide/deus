# Use a imagem oficial Python slim
FROM python:3.10-slim

# 1) Instala dependências de SO necessárias ao MoviePy/FFMPEG
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ffmpeg \
      libsm6 \
      libxext6 \
 && rm -rf /var/lib/apt/lists/*

# 2) Define diretório de trabalho
WORKDIR /app

# 3) Copia só o requirements e instala as libs Python
COPY requirements.txt ./
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 4) Copia todo o seu código para dentro do container
COPY . .

# 5) Exponha a porta usada pela sua Flask app
ENV PORT=5000
EXPOSE 5000

# 6) Comando padrão para iniciar a API
CMD ["python", "main.py"]