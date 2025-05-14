FROM python:3.10-slim

# Instala ffmpeg e libs necessárias ao moviepy
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Instala as dependências do projeto
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=5000
EXPOSE 5000

# Executa seu script principal
CMD ["python", "main.py"]