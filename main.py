import os, uuid, io, csv
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

AUDIO_DIR = Path("audio")
CSV_DIR = Path("csv")
AUDIO_DIR.mkdir(exist_ok=True)
CSV_DIR.mkdir(exist_ok=True)

# Chaves
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GOOGLE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")

client = OpenAI(api_key=OPENAI_API_KEY)

# Credenciais do Drive
creds = service_account.Credentials.from_service_account_file(
    "credentials.json",
    scopes=["https://www.googleapis.com/auth/drive"]
)
drive = build("drive", "v3", credentials=creds)

def upload_to_drive(file_path, mime_type):
    file_metadata = {
        "name": Path(file_path).name,
        "parents": [GOOGLE_FOLDER_ID]
    }
    media = MediaIoBaseUpload(open(file_path, "rb"), mimetype=mime_type)
    file = drive.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = file["id"]
    drive.permissions().create(fileId=file_id, body={"role": "reader", "type": "anyone"}).execute()
    return f"https://drive.google.com/uc?id={file_id}"

def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN"):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
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
    return jsonify({"audio_url": upload_to_drive(path, "audio/mpeg")})

def _get_audio_file(audio_url):
    if audio_url.startswith("http"):
        resp = requests.get(audio_url, timeout=60)
        resp.raise_for_status()
        buf = io.BytesIO(resp.content)
        buf.name = "remoto.mp3"
        return buf
    return open(audio_url, "rb")

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

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json(force=True, silent=True) or {}
    transcricao = data.get("transcricao", [])
    if not transcricao:
        return jsonify({"error": "campo 'transcricao' obrigatório"}), 400

    filename = f"{uuid.uuid4()}.csv"
    path = CSV_DIR / filename

    with open(path, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["IMG", "SEGUNDO"])
        for i, bloco in enumerate(transcricao, start=1):
            segundo = round(bloco["inicio"])
            writer.writerow([i, segundo])

    return jsonify({"csv_url": upload_to_drive(path, "text/csv")})

if __name__ == "__main__":
    app.run()
