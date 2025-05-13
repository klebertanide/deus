# Usa imagem oficial Python 3.11
FROM python:3.11-slim

WORKDIR /app

# Instala dependências de SO
RUN apt-get update && apt-get install -y \
    ffmpeg libsm6 libxext6 git \
    && rm -rf /var/lib/apt/lists/*

# Copia requisitos e instala PyTorch CPU
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir \
    https://download.pytorch.org/whl/cpu/torch-2.2.2%2Bcpu-cp311-cp311-linux_x86_64.whl

# Instala demais dependências
RUN pip install --no-cache-dir -r requirements.txt

# Copia código
COPY . /app

EXPOSE 5000

CMD ["python", "main.py"]
