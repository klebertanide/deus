import os, uuid, io, csv, tempfile
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

# Diretórios
AUDIO_DIR = Path("audio"); AUDIO_DIR.mkdir(exist_ok=True)
CSV_DIR = Path("csv"); CSV_DIR.mkdir(exist_ok=True)

# Chaves e clientes
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
client = OpenAI(api_key=OPENAI_KEY)

# Autentica Google Drive
creds = service_account.Credentials.from_service_account_file("credentials.json", scopes=["https://www.googleapis.com/auth/drive"])
drive_service = build("drive", "v3", credentials=creds)

def upload_para_drive(path):
    file_metadata = {"name": path.name, "parents": [DRIVE_FOLDER_ID]}
    media = MediaFileUpload(str(path), resumable=True)
    file = drive_service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    file_id = file.get("id")
    drive_service.permissions().create(fileId=file_id, body={"role": "reader", "type": "anyone"}).execute()
    return f"https://drive.google.com/uc?id={file_id}&export=download"

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
    data = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify({"error": "campo 'texto' obrigatório"}), 400
    audio_bytes = elevenlabs_tts(texto)
    filename = f"{uuid.uuid4()}.mp3"
    path = AUDIO_DIR / filename
    with open(path, "wb") as f:
        f.write(audio_bytes)
    link = upload_para_drive(path)
    return jsonify({"audio_url": link})

# ===== /enviar_mp3 =====
@app.route("/enviar_mp3", methods=["POST"])
def enviar_mp3():
    data = request.get_json() or {}
    url = data.get("url")
    if not url:
        return jsonify({"error": "campo 'url' obrigatório"}), 400
    try:
        r = requests.get(url, timeout=60)
        r.raise_for_status()
        temp = AUDIO_DIR / f"{uuid.uuid4()}.mp3"
        with open(temp, "wb") as f:
            f.write(r.content)
        link = upload_para_drive(temp)
        return jsonify({"audio_salvo_em": link, "nome_local": temp.name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ===== /transcrever =====
def _get_audio_file(audio_url):
    if audio_url.startswith("http"):
        resp = requests.get(audio_url, timeout=60)
        resp.raise_for_status()
        buf = io.BytesIO(resp.content); buf.name = "remote.mp3"
        return buf
    return open(AUDIO_DIR / audio_url, "rb")

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json() or {}
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

# ===== /gerar_csv (simplificado) =====
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json() or {}
    conteudo = data.get("linhas")
    if not conteudo or not isinstance(conteudo, list):
        return jsonify({"error": "campo 'linhas' obrigatório (lista)"}), 400

    filename = f"{uuid.uuid4()}.csv"
    path = CSV_DIR / filename
    with open(path, "w", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["imagem", "texto"])
        for linha in conteudo:
            writer.writerow(linha)

    link = upload_para_drive(path)
    return jsonify({"csv_url": link})

# ====== Run local =======
if __name__ == "__main__":
    app.run(debug=True)
