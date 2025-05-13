# 1) imagem base mais enxuta
FROM python:3.11-slim

WORKDIR /app

# 2) copiar só o requirements para maximizar cache
COPY requirements.txt /app/

# 3) instalar bibliotecas de sistema e PyTorch CPU
RUN apt-get update && apt-get install -y \
      ffmpeg libsm6 libxext6 git \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir \
         https://download.pytorch.org/whl/cpu/torch-2.2.2%2Bcpu-cp311-cp311-linux_x86_64.whl \
    && pip install --no-cache-dir -r requirements.txt

# 4) agora copie todo o resto do seu código (incluindo .well-known)
COPY . /app

# 5) porta do Flask
EXPOSE 5000

# 6) comando de inicialização
CMD ["python", "main.py"]
