FROM python:3.11
WORKDIR /app
COPY . /app
RUN apt-get update && apt-get install -y ffmpeg libsm6 libxext6 git && rm -rf /var/lib/apt/lists/*
RUN pip install --upgrade pip
RUN pip install torch==2.2.2+cpu torchaudio==2.2.2+cpu -f https://download.pytorch.org/whl/torch_stable.html
RUN pip install -r requirements.txt
EXPOSE 5000
CMD ["python", "main.py"]