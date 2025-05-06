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

AUDIO_DIR = Path("audio")
CSV_DIR = Path("csv")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR.mkdir(parents=True, exist_ok=True)

# Chaves
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
DRIVE_FOLDER_ID = os.getenv("DRIVE_FOLDER_ID")
GOOGLE_JSON = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")

client = OpenAI(api_key=OPENAI_KEY)
drive_creds = service_account.Credentials.from_service_account_file(
    GOOGLE_JSON, scopes=["https://www.googleapis.com/auth/drive.file"]
)
drive = build("drive", "v3", credentials=drive_creds)

def upload_to_drive(file_path, filename, mimetype):
    media = MediaFileUpload(file_path, mimetype=mimetype)
    body = {"name": filename, "parents": [DRIVE_FOLDER_ID]}
    file = drive.files().create(body=body, media_body=media, fields="id").execute()
    return f"https://drive.google.com/uc?id={file['id']}"

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

    url_local = request.url_root.rstrip("/") + "/audio/" + filename
    url_drive = upload_to_drive(str(path), filename, "audio/mpeg")
    return jsonify({"audio_url": url_local, "drive_backup": url_drive})

# ===== /transcrever =====
def _get_audio_file(audio_url):
    if audio_url.startswith(request.url_root.rstrip('/')):
        fname = audio_url.split('/audio/')[-1]
        p = AUDIO_DIR / fname
        if p.exists():
            return open(p, 'rb')
    resp = requests.get(audio_url, timeout=60)
    resp.raise_for_status()
    buf = io.BytesIO(resp.content)
    buf.name = "remote.mp3"
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
    finally:
        try:
            audio_file.close()
        except:
            pass

# ===== /gerar_csv =====
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
        writer.writerow(["imagem", "tempo"])
        for i, bloco in enumerate(transcricao, start=1):
            tempo_segundos = int(round(bloco["inicio"]))
            writer.writerow([i, tempo_segundos])

    url_local = request.url_root.rstrip("/") + "/csv/" + filename
    url_drive = upload_to_drive(str(path), filename, "text/csv")
    return jsonify({"csv_url": url_local, "drive_backup": url_drive})

# ===== Arquivos estáticos =====
@app.route("/audio/<path:filename>")
def baixar_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

@app.route("/csv/<path:filename>")
def baixar_csv(filename):
    return send_from_directory(CSV_DIR, filename)

if __name__ == "__main__":
    app.run(debug=True)
