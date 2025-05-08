# Usa uma imagem base com Python 3.11
FROM python:3.11-slim

# Evita prompts durante instalação
ENV DEBIAN_FRONTEND=noninteractive
# Instala dependências básicas
RUN apt-get update && apt-get install -y curl wget gnupg unzip git \
 && apt-get clean

# Instala dependências do sistema (inclui navegador pro Selenium)
RUN apt-get update && apt-get install -y \
    wget unzip gnupg curl fonts-liberation libnss3 libxss1 libasound2 \
    libatk-bridge2.0-0 libgtk-3-0 libgbm1 chromium chromium-driver \
    && rm -rf /var/lib/apt/lists/*
# Instala o Chrome para Playwright
RUN apt-get update && apt-get install -y ca-certificates fonts-liberation libappindicator3-1 libasound2 libatk-bridge2.0-0 libatk1.0-0 libcups2 libdbus-1-3 libgdk-pixbuf2.0-0 libnspr4 libnss3 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 xdg-utils libu2f-udev libvulkan1

# Define diretório de trabalho
# Cria diretório de trabalho
WORKDIR /app

# Copia os arquivos da aplicação
# Copia os arquivos do projeto
COPY . .

# Instala dependências Python
# Instala as dependências do projeto
RUN pip install --upgrade pip && pip install -r requirements.txt

# Expõe a porta usada pela aplicação
EXPOSE 3000
# Instala o Playwright e seus navegadores
RUN playwright install chromium

# Comando para iniciar a aplicação
CMD ["python", "main.py"]
