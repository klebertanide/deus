# Usa imagem base com Python 3.11
FROM python:3.11

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Copia todos os arquivos do projeto
COPY . /app
COPY .well-known /app/.well-known

# Instala dependências do sistema necessárias para MoviePy, imageio, Pillow, etc.
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    git \
    && rm -rf /var/lib/apt/lists/*

# Instala versão compatível do PyTorch com CPU e Python 3.11
RUN pip install --no-cache-dir \
    https://download.pytorch.org/whl/cpu/torch-2.2.2%2Bcpu-cp311-cp311-linux_x86_64.whl

# Instala as demais dependências do projeto
RUN pip install --no-cache-dir -r requirements.txt

# Expõe a porta padrão do Flask
EXPOSE 5000

# Comando para rodar a aplicação
CMD ["python", "main.py"]