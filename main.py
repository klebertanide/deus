import os
import uuid
import io
import csv
import re
import zipfile
import time
import requests
import unidecode
import numpy as np
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import openai
from moviepy.editor import (
    AudioFileClip, ImageClip, TextClip, CompositeVideoClip,
    concatenate_videoclips, VideoFileClip
)
from moviepy.video.VideoClip import VideoClip
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# ——————————————— Configurações de pastas ———————————————
BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR = BASE / "csv"
FILES_DIR = BASE / "downloads"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY     = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_KEY

# ——————————————— Helpers ———————————————
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
        "voice_settings": {
            "stability": 0.60,
            "similarity_boost": 0.90,
            "style": 0.15,
            "use_speaker_boost": True
        },
        "model_id": "eleven_multilingual_v2",
        "voice_id": "cwIsrQsWEVTols6slKYN"  # voz Abujamra
    }
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
        headers=headers, json=payload
    )
    r.raise_for_status()
    return r.content

def make_grain(size=(1280, 720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity, 128+intensity,
                                  (size[1], size[0], 1), dtype=np.uint8)
        noise = np.repeat(noise, 3, axis=2)
        return noise
    return VideoClip(frame, duration=1).set_fps(24)

# ——————————————— Rotas públicas ———————————————
@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

@app.route("/audio/<path:fn>")
def serve_audio(fn):
    return send_from_directory(AUDIO_DIR, fn)

@app.route("/csv/<path:fn>")
def serve_csv(fn):
    return send_from_directory(CSV_DIR, fn)

@app.route("/downloads/<path:fn>")
def serve_download(fn):
    return send_from_directory(FILES_DIR, fn)

# ——————————————— /falar ———————————————
@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug     = slugify(texto)
    filename = f"{slug}.mp3"
    outpath  = AUDIO_DIR / filename

    try:
        audio_bytes = elevenlabs_tts(texto)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    with open(outpath, "wb") as f:
        f.write(audio_bytes)

    return jsonify(
        audio_url = request.url_root.rstrip("/") + f"/audio/{filename}",
        filename  = filename,
        slug      = slug
    )

# ——————————————— /transcrever ———————————————
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    if audio_url.startswith(request.url_root.rstrip("/")):
        fname = audio_url.rsplit("/audio/", 1)[-1]
        file  = open(AUDIO_DIR / fname, "rb")
    else:
        resp = requests.get(audio_url, timeout=60)
        resp.raise_for_status()
        file = io.BytesIO(resp.content)
        file.name = audio_url.rsplit("/audio/", 1)[-1]

    try:
        srt = openai.audio.transcriptions.create(
            model="whisper-1",
            file=file,
            response_format="srt"
        )
        def parse_ts(ts):
            h,m,rest = ts.split(":")
            s,ms     = rest.split(",")
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

        segmentos = []
        for bloco in srt.strip().split("\n\n"):
            lines = bloco.split("\n")
            if len(lines) < 3: continue
            start, end = lines[1].split(" --> ")
            texto      = " ".join(lines[2:])
            segmentos.append({
                "inicio": parse_ts(start),
                "fim":    parse_ts(end),
                "texto":  texto
            })

        return jsonify(duracao_total=segmentos[-1]["fim"], transcricao=segmentos)

    except Exception as e:
        print("[ERRO WHISPER]", e)
        import traceback; traceback.print_exc()
        return jsonify(error=str(e)), 500
    finally:
        try: file.close()
        except: pass

# ——————————————— /gerar_csv ———————————————
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json() or {}
    transcricao     = data.get("transcricao", [])
    prompts         = data.get("prompts", [])
    descricao       = data.get("descricao", "")
    texto_original  = data.get("texto_original", "")

    slug = slugify(texto_original or descricao)
    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    drive    = get_drive_service()
    folderId = criar_pasta_drive(slug, drive)

    csv_path  = FILES_DIR / f"{slug}.csv"
    srt_path  = FILES_DIR / f"{slug}.srt"
    txt_path  = FILES_DIR / f"{slug}.txt"
    mp3_path  = AUDIO_DIR  / f"{slug}.mp3"

    header = [
        "TIME","PROMPT","VISIBILITY","ASPECT_RATIO",
        "MAGIC_PROMPT","MODEL","SEED","RENDERING",
        "NEGATIVE","STYLE","PALETTE"
    ]
    neg = "low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy"

    # CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for seg, p in zip(transcricao, prompts):
            t = int(seg["inicio"])
            w.writerow([t, f"{t} - {p}", "PRIVATE", "9:16", "ON", "3.0", "", "TURBO", neg, "AUTO", ""])

    # SRT
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(transcricao, 1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")

    # TXT
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(descricao.strip())

    # Upload dos arquivos
    upload_arquivo_drive(csv_path, f"{slug}.csv", folderId, drive)
    upload_arquivo_drive(srt_path, f"{slug}.srt", folderId, drive)
    upload_arquivo_drive(txt_path, f"{slug}.txt", folderId, drive)

    # Upload do MP3, com verificação
    if mp3_path.exists():
        upload_arquivo_drive(mp3_path, f"{slug}.mp3", folderId, drive)
    else:
        print(f"[AVISO] MP3 não encontrado: {mp3_path}")

    return jsonify(slug=slug, folder_url=f"https://drive.google.com/drive/folders/{folderId}")

# ——————————————— restante das rotas ———————————————
@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify(error="Campo 'zip' obrigatório."), 400
    # ... resto permanece igual ...
    return jsonify(ok=True, slug=slug, usadas=selecionadas)

@app.route("/montar_video", methods=["POST"])
def montar_video():
    # ... permanece igual ...
    return jsonify(video_url=f"https://drive.google.com/drive/folders/{folderId}")

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)
