import os, uuid, io, tempfile, csv
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

load_dotenv()
app = Flask(__name__)

# Pastas locais (opcional, para fallback/testes)
AUDIO_DIR = Path("audio")
CSV_DIR = Path("csv")
AUDIO_DIR.mkdir(exist_ok=True)
CSV_DIR.mkdir(exist_ok=True)

# Google Drive
DRIVE_FOLDER_ID = "18rmQa-kSLRdPROAMBKQyFR6vtzXIR0gI"
SCOPES = ["https://www.googleapis.com/auth/drive.file"]
CREDENTIALS_FILE = "credentials.json"

credentials = service_account.Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
drive_service = build("drive", "v3", credentials=credentials)

# ElevenLabs + OpenAI
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def upload_to_drive(file_bytes, filename, mimetype):
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mimetype, resumable=True)
    file_metadata = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
    file = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = file.get("id")
    return f"https://drive.google.com/uc?id={file_id}&export=download"

# ===== /upload_audio =====
@app.route("/upload_audio", methods=["POST"])
def upload_audio():
    if "file" not in request.files:
        return jsonify({"error": "Arquivo MP3 não enviado"}), 400

    file = request.files["file"]
    if not file.filename.endswith(".mp3"):
        return jsonify({"error": "Formato inválido. Envie um arquivo .mp3"}), 400

    audio_bytes = file.read()
    filename = f"{uuid.uuid4()}.mp3"
    try:
        public_url = upload_to_drive(audio_bytes, filename, "audio/mpeg")
        return jsonify({"audio_url": public_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
    try:
        public_url = upload_to_drive(audio_bytes, filename, "audio/mpeg")
        return jsonify({"audio_url": public_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===== /transcrever =====
def _get_audio_file(audio_url):
    resp = requests.get(audio_url, timeout=60)
    resp.raise_for_status()
    buf = io.BytesIO(resp.content)
    buf.name = "audio.mp3"
    return buf

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
    transcricao = data.get("transcricao", [])
    if not transcricao:
        return jsonify({"error": "lista 'transcricao' obrigatória"}), 400

    filename = f"{uuid.uuid4()}.csv"
    path = CSV_DIR / filename

    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for i, bloco in enumerate(transcricao):
            segundo = int(bloco["inicio"])
            writer.writerow([i + 1, segundo])

    with open(path, "rb") as f:
        csv_bytes = f.read()
    try:
        public_url = upload_to_drive(csv_bytes, filename, "text/csv")
        return jsonify({"csv_url": public_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===== Run local =====
if __name__ == "__main__":
    app.run(debug=True)
