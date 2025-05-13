# Usa Python 3.11 slim
FROM python:3.11-slim

WORKDIR /app

# Instala libs de sistema
RUN apt-get update && apt-get install -y \
    ffmpeg libsm6 libxext6 git \
  && rm -rf /var/lib/apt/lists/*

# Copia fonte
COPY . /app

# Instala PyTorch CPU
RUN pip install --no-cache-dir \
    https://download.pytorch.org/whl/cpu/torch-2.3.1%2Bcpu-cp311-cp311-linux_x86_64.whl \
    torchaudio==2.3.1

# Instala dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Expõe porta
EXPOSE 5000

CMD ["python", "main.py"]
