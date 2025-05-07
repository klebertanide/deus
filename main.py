import os, uuid, io, tempfile, json
import requests
from flask import Flask, request, jsonify
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

load_dotenv()
app = Flask(__name__)

# Diretórios
TMP_DIR = Path("tmp")
TMP_DIR.mkdir(parents=True, exist_ok=True)

# APIs
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")

# Google Drive
SCOPES = ["https://www.googleapis.com/auth/drive"]
CREDENTIALS_PATH = "credentials.json"
FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")  # exemplo: "18rmQa-kSLRdPROAMBKQyFR6vtzXIR0gI"

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)

def upload_to_drive(filename, bytes_data, mimetype="audio/mpeg"):
    file_metadata = {
        "name": filename,
        "parents": [FOLDER_ID]
    }
    media = MediaIoBaseUpload(io.BytesIO(bytes_data), mimetype=mimetype)
    service = get_drive_service()
    uploaded = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = uploaded.get("id")
    return f"https://drive.google.com/uc?id={file_id}&export=download"

# ============ /upload_credentials ============
@app.route("/upload_credentials", methods=["POST"])
def upload_credentials():
    file = request.files.get("file")
    if not file:
        return jsonify({"error": "arquivo não enviado"}), 400
    path = Path(CREDENTIALS_PATH)
    file.save(path)
    return jsonify({"status": "credentials.json salvo com sucesso"})

# ============ /falar ============
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
            "stability": 0.6,
            "similarity_boost": 0.9,
            "style": 0.15,
            "use_speaker_boost": True
        }
    }
    r = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
    r.raise_for_status()
    audio_bytes = r.content

    filename = f"{uuid.uuid4()}.mp3"
    link = upload_to_drive(filename, audio_bytes)
    return jsonify({"audio_url": link})

# ============ /transcrever ============
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True, silent=True) or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error": "campo 'audio_url' obrigatório"}), 400
    try:
        response = requests.get(audio_url)
        response.raise_for_status()
        file = io.BytesIO(response.content)
        file.name = "audio.mp3"
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=file,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )
        duration = transcript.duration
        segments = [{"inicio": seg.start, "fim": seg.end, "texto": seg.text} for seg in transcript.segments]
        return jsonify({"duracao_total": duration, "transcricao": segments})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============ /gerar_csv ============
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json(force=True, silent=True) or {}
    transcricao = data.get("transcricao")
    if not transcricao:
        return jsonify({"error": "campo 'transcricao' obrigatório"}), 400

    csv_text = "imagem,tempo\n"
    for i, bloco in enumerate(transcricao):
        tempo = round(bloco.get("inicio", 0))
        csv_text += f"{i+1},{tempo}\n"

    file_bytes = csv_text.encode("utf-8")
    filename = f"{uuid.uuid4()}.csv"
    link = upload_to_drive(filename, file_bytes, mimetype="text/csv")
    return jsonify({"csv_url": link})

# ============ Run =============
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))
