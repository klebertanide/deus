import os
import io
import csv
import re
import requests
import unidecode
import json
import uuid
import math
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from openai import OpenAI
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

GOOGLE_DRIVE_ROOT_FOLDER = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
SERVICE_ACCOUNT_FILE     = "/etc/secrets/service_account.json"
ELEVEN_API_KEY           = os.getenv("ELEVENLABS_API_KEY")

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_pasta_se_preciso(pasta_alvo, drive):
    try:
        drive.files().get(fileId=pasta_alvo, fields="id").execute()
    except HttpError:
        meta = {
            "name": "DEUS_TTS_AUTOGERADA",
            "mimeType": "application/vnd.google-apps.folder"
        }
        pasta_alvo = drive.files().create(body=meta).execute()["id"]
    return pasta_alvo

def criar_subpasta(slug: str, drive, parent_folder_id: str):
    meta = {
        "name": slug,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id]
    }
    return drive.files().create(body=meta).execute()["id"]

def upload_para_drive(path: Path, nome: str, folder_id: str, drive):
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(
        body={"name": nome, "parents": [folder_id]},
        media_body=media
    ).execute()

def gerar_slug():
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:6]

def slugify(text: str, limit: int = 30) -> str:
    txt = unidecode.unidecode(text or "")
    txt = re.sub(r"[^\w\s]", "", txt)
    txt = txt.strip().replace(" ", "_").lower()
    return txt[:limit] if txt else gerar_slug()

def elevenlabs_tts(text: str) -> bytes:
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "voice_settings": {
            "stability": 0.6,
            "similarity_boost": 0.9,
            "style": 0.15,
            "use_speaker_boost": True
        },
        "model_id": "eleven_multilingual_v2",
        "voice_id":  "cwIsrQsWEVTols6slKYN"
    }
    for tentativa in range(2):
        try:
            r = requests.post(
                "https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
                headers=headers,
                json=payload,
                timeout=60
            )
            r.raise_for_status()
            return r.content
        except Exception as e:
            if tentativa == 1:
                raise e

def parse_ts(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json(force=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug = slugify(texto)
    mp3_path = Path("saida") / f"{slug}_audio.mp3"
    mp3_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        if not ELEVEN_API_KEY:
            raise Exception("ELEVEN_API_KEY não está definido")
        audio_bytes = elevenlabs_tts(texto)
        if not audio_bytes or len(audio_bytes) < 1000:
            raise Exception("Áudio gerado é vazio ou muito pequeno.")
        mp3_path.write_bytes(audio_bytes)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    try:
        drive = get_drive_service()
        root_folder = criar_pasta_se_preciso(GOOGLE_DRIVE_ROOT_FOLDER, drive)
        subfolder_id = criar_subpasta(slug, drive, root_folder)
        upload_para_drive(mp3_path, mp3_path.name, subfolder_id, drive)
    except Exception as e:
        return jsonify(error="falha no upload do MP3 para o Drive", detalhe=str(e)), 500

    return jsonify(
        audio_url=str(mp3_path.resolve()),
        slug=slug,
        drive_folder_url=f"https://drive.google.com/drive/folders/{subfolder_id}"
    )

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True) or {}
    audio_ref = data.get("audio_url") or data.get("audio_file")
    if not audio_ref:
        return jsonify(error="campo 'audio_url' ou 'audio_file' obrigatório"), 400

    try:
        if os.path.exists(audio_ref):
            fobj = open(audio_ref, "rb")
        else:
            resp = requests.get(audio_ref, timeout=60)
            resp.raise_for_status()
            fobj = io.BytesIO(resp.content)
            fobj.name = Path(audio_ref).name or "audio.mp3"
    except Exception as e:
        return jsonify(error="falha ao carregar áudio", detalhe=str(e)), 400

    try:
        raw_srt = client.audio.transcriptions.create(
            model="whisper-1",
            file=fobj,
            response_format="srt"
        )
        blocks = []
            for blk in raw_srt.strip().split("\n\n"):
            parts = blk.split("\n")
            if len(parts) < 3:
        continue
            st, en = parts[1].split(" --> ")
            txt = " ".join(parts[2:])
            inicio = parse_ts(st)
            fim = parse_ts(en)
            blocks.append((inicio, fim, txt))

        total = blocks[-1][1] if blocks else 0
        return jsonify(transcricao=[{"inicio": i, "fim": f, "texto": t} for i, f, t in blocks], duracao_total=total)
    except Exception as e:
        return jsonify(error="falha na transcrição", detalhe=str(e)), 500
    finally:
        try:
            fobj.close()
        except:
            pass

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
