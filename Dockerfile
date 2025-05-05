# Usa uma imagem base com Python 3.11
FROM python:3.11-slim

# Evita prompts durante instalação
ENV DEBIAN_FRONTEND=noninteractive

# Instala dependências do sistema (inclui navegador pro Selenium)
RUN apt-get update && apt-get install -y \
    wget unzip gnupg curl fonts-liberation libnss3 libxss1 libasound2 \
    libatk-bridge2.0-0 libgtk-3-0 libgbm1 chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Define diretório de trabalho
WORKDIR /app

# Copia os arquivos da aplicação
COPY . .

# Instala dependências Python
RUN pip install --upgrade pip && pip install -r requirements.txt

# Expõe a porta usada pela aplicação
EXPOSE 3000

# Comando para iniciar a aplicação
CMD ["python", "main.py"]
