FROM python:3.10-slim

# (removido ffmpeg, já que não há vídeo)
# COPY tudo para a raiz
COPY . .

# Instala dependências
RUN pip install --no-cache-dir -r requirements.txt

ENV PORT=5000
EXPOSE 5000

CMD ["python", "main.py"]
