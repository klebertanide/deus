FROM python:3.10-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Copia tudo para a raiz do container
COPY . .

# Instala as dependências
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=5000
EXPOSE 5000

# Executa main.py na raiz
CMD ["python", "main.py"]