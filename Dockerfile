# Usa imagem leve do Python
FROM python:3.10-slim

# Instala ffmpeg e libs necessárias ao moviepy
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Define diretório padrão
WORKDIR /app

# Copia todos os arquivos do repositório
COPY . .

# Atualiza pip e instala dependências
RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

# Define variáveis padrão
ENV PORT=5000
EXPOSE 5000

# Inicia a aplicação (ajuste aqui se renomear o arquivo)
CMD ["python", "main.py"]
