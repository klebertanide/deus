FROM python:3.11-slim

# Instala dependências básicas
RUN apt-get update && apt-get install -y curl wget gnupg unzip git \
 && apt-get clean

# Instala o Chrome para Playwright
RUN apt-get update && apt-get install -y ca-certificates fonts-liberation libappindicator3-1 libasound2 libatk-bridge2.0-0 libatk1.0-0 libcups2 libdbus-1-3 libgdk-pixbuf2.0-0 libnspr4 libnss3 libx11-xcb1 libxcomposite1 libxdamage1 libxrandr2 xdg-utils libu2f-udev libvulkan1

# Cria diretório de trabalho
WORKDIR /app

# Copia os arquivos do projeto
COPY . .

# Instala as dependências do projeto
RUN pip install --upgrade pip && pip install -r requirements.txt

# Instala o Playwright e seus navegadores
RUN playwright install chromium

CMD ["python", "main.py"]
