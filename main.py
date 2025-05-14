import os
import io
import re
import csv
import uuid
import unidecode
import requests
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import openai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# ======================== CONFIG ========================
BASE = Path(".")
AUDIO_DIR = BASE / "downloads"
CSV_DIR = BASE / "downloads"
FILES_DIR = BASE / "downloads"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY     = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_KEY

# ======================== HELPERS ========================
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_pasta_drive(nome, drive):
    file_metadata = {
        'name': nome,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [GOOGLE_DRIVE_FOLDER_ID]
    }
    folder = drive.files().create(body=file_metadata, fields='id').execute()
    return folder['id']

def upload_arquivo_drive(path, nome, folder_id, drive):
    file_metadata = {
        'name': nome,
        'parents': [folder_id]
    }
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(body=file_metadata, media_body=media).execute()

def slugify(text, limit=30):
    txt = unidecode.unidecode(text)
    txt = re.sub(r"[^\w\s]", "", txt)
    return txt.strip().replace(" ", "_").lower()[:limit]

def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def elevenlabs_tts(texto):
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": texto,
        "voice_settings": {"stability": 0.4, "similarity_boost": 0.7},
        "model_id": "eleven_monolingual_v1",
        "voice_id": "EXAVITQu4vr4xnSDxMaL"  # troque se quiser outra voz
    }
    r = requests.post("https://api.elevenlabs.io/v1/text-to-speech/EXAVITQu4vr4xnSDxMaL",
                      headers=headers, json=payload)
    r.raise_for_status()
    return r.content

# ======================== ENDPOINTS ========================
@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug = slugify(texto)
    filename = f"{slug}.mp3"
    outpath = AUDIO_DIR / filename

    try:
        audio_bytes = elevenlabs_tts(texto)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    with open(outpath, "wb") as f:
        f.write(audio_bytes)

    return jsonify(audio_url=request.url_root.rstrip("/") + f"/audio/{filename}", filename=filename, slug=slug)

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    if audio_url.startswith(request.url_root.rstrip("/")):
        fname = audio_url.rsplit("/audio/", 1)[-1]
        file = open(AUDIO_DIR / fname, "rb")
    else:
        resp = requests.get(audio_url, timeout=60)
        resp.raise_for_status()
        file = io.BytesIO(resp.content); file.name = "audio.mp3"

    try:
        srt = openai.audio.transcriptions.create(
            model="whisper-1",
            file=file,
            response_format="srt"
        )
        def parse_ts(ts):
            h,m,rest = ts.split(":")
            s,ms = rest.split(",")
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

        segmentos = []
        for bloco in srt.strip().split("\n\n"):
            lines = bloco.split("\n")
            if len(lines) < 3: continue
            start, end = lines[1].split(" --> ")
            texto = " ".join(lines[2:])
            segmentos.append({"inicio": parse_ts(start), "fim": parse_ts(end), "texto": texto})

        return jsonify(duracao_total=segmentos[-1]["fim"], transcricao=segmentos)

    except Exception as e:
        return jsonify(error=str(e)), 500

    finally:
        try: file.close()
        except: pass

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json() or {}
    transcricao = data.get("transcricao", [])
    prompts = data.get("prompts", [])
    descricao = data.get("descricao", "")
    texto_original = data.get("texto_original", "")

    slug = slugify(texto_original or descricao)

    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    drive = get_drive_service()
    folderId = criar_pasta_drive(slug, drive)

    csv_path = FILES_DIR / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"

    header = ["TIME","PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL","SEED","RENDERING","NEGATIVE","STYLE","PALETTE"]
    neg = "low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy"

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for seg,p in zip(transcricao, prompts):
            t = int(seg["inicio"])
            prompt_final = f"{t} - {p}"
            w.writerow([t, prompt_final, "PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    with open(srt_path, "w", encoding="utf-8") as f:
        for i,seg in enumerate(transcricao,1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(descricao.strip())

    upload_arquivo_drive(csv_path, "prompts.csv", folderId, drive)
    upload_arquivo_drive(srt_path, "legenda.srt", folderId, drive)
    upload_arquivo_drive(txt_path, "descricao.txt", folderId, drive)

    return jsonify(slug=slug, folder_url=f"https://drive.google.com/drive/folders/{folderId}")

@app.route("/audio/<path:fn>")
def serve_audio(fn):
    return send_from_directory(AUDIO_DIR, fn)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
