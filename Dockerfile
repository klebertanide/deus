# Imagem base
FROM python:3.10-slim

# Diretório de trabalho
WORKDIR /app

# Copia os arquivos do projeto
COPY . /app

# Instala dependências
RUN pip install --no-cache-dir -r requirements.txt

# Expõe a porta
EXPOSE 5000

# Variáveis de ambiente (substitua no deploy ou use .env)
ENV FLASK_ENV=production
ENV PORT=5000

# Comando para rodar a API
CMD ["python", "main.py"]
