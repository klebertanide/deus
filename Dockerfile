FROM python:3.10-slim

# Instala dependências de sistema necessárias ao MoviePy
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copia todo o código para dentro do container
COPY . .

# Instala as dependências Python
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=5000
EXPOSE 5000

# Inicia o servidor Flask
CMD ["python", "main.py"]
