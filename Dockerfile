FROM python:3.11-slim

# Instala dependências básicas
RUN apt-get update && apt-get install -y build-essential curl git && apt-get clean

# Cria diretório de trabalho
WORKDIR /app

# Copia os arquivos do projeto
COPY . .

# Instala dependências do projeto
RUN pip install --upgrade pip && pip install -r requirements.txt

# Expõe a porta padrão do Flask
EXPOSE 8080

# Inicia o app
CMD ["python", "main.py"]