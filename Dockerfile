FROM python:3.11-slim

WORKDIR /app

# Copia tudo, incluindo .well-known
COPY . /app

RUN apt-get update && apt-get install -y \
    ffmpeg libsm6 libxext6 git \
  && rm -rf /var/lib/apt/lists/*

# PyTorch CPU-only
RUN pip install --no-cache-dir \
    https://download.pytorch.org/whl/cpu/torch-2.2.2%2Bcpu-cp311-cp311-linux_x86_64.whl \
    torchaudio==2.0.2

# Resto das libs
RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 5000
CMD ["python", "main.py"]
