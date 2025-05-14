import os
import io
import csv
import re
import uuid
import requests
import unidecode
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import openai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# — Configurações de pastas —
BASE       = Path(".")
AUDIO_DIR  = BASE / "audio"
CSV_DIR    = BASE / "csv"
FILES_DIR  = BASE / "downloads"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY        = os.getenv("ELEVENLABS_API_KEY")
openai.api_key        = os.getenv("OPENAI_API_KEY")

# — Helpers —
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_pasta_drive(nome, drive):
    meta = {'name': nome, 'mimeType': 'application/vnd.google-apps.folder',
            'parents': [GOOGLE_DRIVE_FOLDER_ID]}
    folder = drive.files().create(body=meta, fields='id').execute()
    return folder['id']

def upload_arquivo_drive(path, nome, folder_id, drive):
    meta = {'name': nome, 'parents': [folder_id]}
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(body=meta, media_body=media).execute()

def slugify(text, limit=30):
    txt = unidecode.unidecode(text)
    txt = re.sub(r"[^\w\s]", "", txt)
    return txt.strip().replace(" ", "_").lower()[:limit]

def elevenlabs_tts(text):
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "voice_settings": {
            "stability": 0.60,
            "similarity_boost": 0.90,
            "style": 0.15,
            "use_speaker_boost": True
        },
        "model_id": "eleven_multilingual_v2",
        "voice_id": "cwIsrQsWEVTols6slKYN"
    }
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
        headers=headers, json=payload
    )
    r.raise_for_status()
    return r.content

# — Rotas —
@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

@app.route("/audio/<path:fn>")
def serve_audio(fn):
    return send_from_directory(AUDIO_DIR, fn)

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

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    if audio_url.startswith(request.url_root.rstrip("/")):
        fname = audio_url.rsplit("/audio/",1)[-1]
        file  = open(AUDIO_DIR / fname, "rb")
    else:
        resp = requests.get(audio_url, timeout=60)
        resp.raise_for_status()
        file = io.BytesIO(resp.content); file.name = audio_url.rsplit("/",1)[-1]

    try:
        srt = openai.audio.transcriptions.create(
            model="whisper-1", file=file, response_format="srt"
        )
        def parse_ts(ts):
            h,m,rest = ts.split(":")
            s,ms     = rest.split(",")
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

        segs = []
        for blk in srt.strip().split("\n\n"):
            lines = blk.split("\n")
            if len(lines)<3: continue
            st, en = lines[1].split(" --> ")
            txt    = " ".join(lines[2:])
            segs.append({"inicio": parse_ts(st), "fim": parse_ts(en), "texto": txt})

        return jsonify(duracao_total=segs[-1]["fim"], transcricao=segs)
    except Exception as e:
        return jsonify(error=str(e)), 500
    finally:
        try: file.close()
        except: pass

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data           = request.get_json() or {}
    transcricao    = data.get("transcricao", [])
    prompts        = data.get("prompts", [])
    descricao      = data.get("descricao", "")
    texto_original = data.get("texto_original", "")

    slug = slugify(texto_original or descricao)
    if not transcricao or not prompts or len(transcricao)!=len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    drive    = get_drive_service()
    folderId = criar_pasta_drive(slug, drive)

    csv_path = CSV_DIR  / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"
    mp3_path = AUDIO_DIR / f"{slug}.mp3"

    # CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["TIME","PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL","SEED","RENDERING","NEGATIVE","STYLE","PALETTE"])
        neg = "low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy"
        for seg,p in zip(transcricao,prompts):
            t = int(seg["inicio"])
            w.writerow([t, f"{t} - {p}", "PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT
    def format_ts(s):
        ms = int((s%1)*1000); h=int(s//3600); m=int((s%3600)//60); sec=int(s%60)
        return f"{h:02}:{m:02}:{sec:02},{ms:03}"
    with open(srt_path, "w", encoding="utf-8") as f:
        for i,seg in enumerate(transcricao,1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")

    # TXT
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(descricao.strip())

    # Upload
    upload_arquivo_drive(csv_path, f"{slug}.csv", folderId, drive)
    upload_arquivo_drive(srt_path, f"{slug}.srt", folderId, drive)
    upload_arquivo_drive(txt_path, f"{slug}.txt", folderId, drive)

    if mp3_path.exists():
        upload_arquivo_drive(mp3_path, f"{slug}.mp3", folderId, drive)
    else:
        print(f"[AVISO] MP3 não encontrado: {mp3_path}")

    return jsonify(slug=slug, folder_url=f"https://drive.google.com/drive/folders/{folderId}")

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)
