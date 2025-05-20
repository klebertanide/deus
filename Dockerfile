# Imagem base
FROM python:3.10-slim

# Evita prompts interativos e garante saída colorida nos logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000

# Diretório de trabalho no container
WORKDIR /app

# Copia os arquivos do projeto
COPY . .

# Instala dependências
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Expõe a porta usada pelo Flask
EXPOSE 5000

# Comando para iniciar a aplicação
CMD ["python", "main.py"]