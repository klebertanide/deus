# Dockerfile

# 1) Use a lightweight Python image
FROM python:3.11-slim

# 2) Set working directory
WORKDIR /app

# 3) Install system dependencies for video and image processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsm6 \
    libxext6 \
    git \
  && rm -rf /var/lib/apt/lists/*

# 4) Copy only requirements first (to leverage Docker cache)
COPY requirements.txt .

# 5) Install PyTorch CPU wheel and matching torchaudio
RUN pip install --no-cache-dir \
    https://download.pytorch.org/whl/cpu/torch-2.3.1%2Bcpu-cp311-cp311-linux_x86_64.whl \
    torchaudio==2.3.1

# 6) Install the rest of the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# 7) Copy the application code
COPY . .

# 8) Expose the Flask port
EXPOSE 5000

# 9) Launch the application
CMD ["python", "main.py"]
