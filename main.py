import os, uuid, io, tempfile
import requests
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
import csv

load_dotenv()
app = Flask(__name__)

AUDIO_DIR = Path("audio")
CSV_DIR = Path("csv")
SRT_DIR = Path("srt")
ZIP_DIR = Path("zips")

for d in [AUDIO_DIR, CSV_DIR, SRT_DIR, ZIP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def upload_to_transfersh(path):
    with open(path, "rb") as f:
        response = requests.put(f"https://transfer.sh/{path.name}", data=f)
        response.raise_for_status()
        return response.text.strip()

@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json(force=True, silent=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify({"error": "campo 'texto' obrigatório"}), 400

    url = f"https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN/stream"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": texto,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.60,
            "similarity_boost": 0.90,
            "style": 0.15,
            "use_speaker_boost": True
        }
    }
    r = requests.post(url, headers=headers, json=payload, stream=True)
    r.raise_for_status()
    filename = f"{uuid.uuid4()}.mp3"
    path = AUDIO_DIR / filename
    with open(path, "wb") as f:
        f.write(r.content)
    public_url = upload_to_transfersh(path)
    return jsonify({"audio_url": public_url})

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True, silent=True) or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error": "campo 'audio_url' obrigatório"}), 400
    try:
        response = requests.get(audio_url, timeout=60)
        response.raise_for_status()
        audio_file = io.BytesIO(response.content)
        audio_file.name = "audio.mp3"

        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )

        segments = transcript.segments
        filename_base = uuid.uuid4().hex

        # CSV simples
        csv_path = CSV_DIR / f"{filename_base}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["imagem", "entrada"])
            for idx, seg in enumerate(segments, 1):
                writer.writerow([idx, round(seg.start)])

        # SRT com limite de 4 palavras por linha
        srt_path = SRT_DIR / f"{filename_base}.srt"
        with open(srt_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(segments, 1):
                palavras = seg.text.strip().split()
                blocos = [" ".join(palavras[i:i+4]) for i in range(0, len(palavras), 4)]
                for j, bloco in enumerate(blocos):
                    ini = seg.start + j * ((seg.end - seg.start) / len(blocos))
                    fim = ini + ((seg.end - seg.start) / len(blocos))
                    ini_str = format_time(ini)
                    fim_str = format_time(fim)
                    f.write(f"{i}-{j+1}\n{ini_str} --> {fim_str}\n{bloco}\n\n")

        # ZIP com arquivos
        zip_path = ZIP_DIR / f"{filename_base}.zip"
        from zipfile import ZipFile
        with ZipFile(zip_path, 'w') as z:
            z.write(csv_path, arcname=csv_path.name)
            z.write(srt_path, arcname=srt_path.name)

        zip_url = upload_to_transfersh(zip_path)
        return jsonify({"zip_url": zip_url})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

def format_time(seconds):
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

if __name__ == "__main__":
    app.run(debug=True)
