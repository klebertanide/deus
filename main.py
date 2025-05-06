import os, uuid, io, csv
import requests
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

load_dotenv()
app = Flask(__name__)

# Pastas locais
AUDIO_DIR = Path("audio"); AUDIO_DIR.mkdir(exist_ok=True)
CSV_DIR = Path("csv"); CSV_DIR.mkdir(exist_ok=True)

# Chaves de API
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_KEY)

# Google Drive
SERVICE_ACCOUNT_FILE = "credentials.json"  # caminho do seu arquivo .json
FOLDER_ID = "18rmQa-kSLRdPROAMBKQyFR6vtzXIR0gI"

def upload_para_drive(caminho_local, nome_final):
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=["https://www.googleapis.com/auth/drive"])
    service = build("drive", "v3", credentials=creds)
    file_metadata = {"name": nome_final, "parents": [FOLDER_ID]}
    media = MediaFileUpload(caminho_local, resumable=True)
    file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return f"https://drive.google.com/file/d/{file['id']}/view"

# ===== /falar =====
def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN"):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.60,
            "similarity_boost": 0.90,
            "style": 0.15,
            "use_speaker_boost": True
        }
    }
    r = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
    r.raise_for_status()
    return r.content

@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json(force=True, silent=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify({"error": "campo 'texto' obrigatório"}), 400
    audio_bytes = elevenlabs_tts(texto)
    filename = f"{uuid.uuid4()}.mp3"
    path = AUDIO_DIR / filename
    with open(path, "wb") as f:
        f.write(audio_bytes)
    drive_url = upload_para_drive(str(path), filename)
    return jsonify({"audio_url": drive_url})

# ===== /transcrever =====
def _get_audio_file(audio_url):
    if audio_url.startswith("http"):
        r = requests.get(audio_url, timeout=60)
        r.raise_for_status()
        buf = io.BytesIO(r.content)
        buf.name = "audio.mp3"
        return buf
    return None

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True, silent=True) or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error": "campo 'audio_url' obrigatório"}), 400
    try:
        audio_file = _get_audio_file(audio_url)
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )
        duration = transcript.duration
        segments = [
            {"inicio": seg.start, "fim": seg.end, "texto": seg.text}
            for seg in transcript.segments
        ]
        return jsonify({"duracao_total": duration, "transcricao": segments})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===== /gerar_csv =====
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json(force=True, silent=True) or {}
    transcricao = data.get("transcricao")
    if not transcricao:
        return jsonify({"error": "campo 'transcricao' obrigatório"}), 400

    filename = f"{uuid.uuid4()}.csv"
    path = CSV_DIR / filename
    with open(path, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        for idx, bloco in enumerate(transcricao):
            segundos = round(bloco["inicio"])
            writer.writerow([idx + 1, segundos])

    drive_url = upload_para_drive(str(path), filename)
    return jsonify({"csv_url": drive_url})

# ===== Run Local =====
if __name__ == "__main__":
    app.run(debug=True)
