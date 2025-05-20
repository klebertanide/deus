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

subpastas_por_slug = {}  # cache temporário por request

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
    global subpastas_por_slug
    slug = nome.split("_")[0]
    if slug in subpastas_por_slug:
        final_folder_id = subpastas_por_slug[slug]
    else:
        final_folder_id = criar_subpasta(slug, drive, folder_id)
        subpastas_por_slug[slug] = final_folder_id

    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(

        body={"name": nome, "parents": [final_folder_id]},
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

@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    global subpastas_por_slug

    data = request.get_json(force=True) or {}
    transcricao = data.get("transcricao")
    prompts = data.get("prompts")
    texto_original = data.get("texto_original")

    if not transcricao or not prompts or not texto_original:
        return jsonify(error="Campos obrigatórios: transcricao, prompts, texto_original"), 400

    slug = slugify(texto_original)
    out_dir = Path("saida")
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / f"{slug}_csv.csv"
    srt_path = out_dir / f"{slug}_legenda.srt"

    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["inicio", "fim", "texto", "prompt"])
            for i, seg in enumerate(transcricao):
                inicio = round(seg["inicio"], 2)
                fim = round(seg["fim"], 2)
                texto = seg["texto"]
                prompt = prompts[i] if i < len(prompts) else ""
                writer.writerow([inicio, fim, texto, prompt])

        with open(srt_path, "w", encoding="utf-8") as f:
            for i, seg in enumerate(transcricao):
                ini = seg["inicio"]
                fim = seg["fim"]
                txt = seg["texto"]
                ts_ini = format_ts(ini)
                ts_fim = format_ts(fim)
                f.write(f"{i+1}
{ts_ini} --> {ts_fim}
{txt}

")

        drive = get_drive_service()
        folder_id = criar_pasta_se_preciso(GOOGLE_DRIVE_ROOT_FOLDER, drive)
        subfolder_id = subpastas_por_slug.get(slug) or criar_subpasta(slug, drive, folder_id)
        subpastas_por_slug[slug] = subfolder_id

        upload_para_drive(csv_path, csv_path.name, folder_id, drive)
        upload_para_drive(srt_path, srt_path.name, folder_id, drive)

        return jsonify(slug=slug, folder_url=f"https://drive.google.com/drive/folders/{subfolder_id}")

    except Exception as e:
        return jsonify(error="Erro ao gerar CSV ou enviar arquivos", detalhe=str(e)), 500


def format_ts(segundos: float) -> str:
    h = int(segundos // 3600)
    m = int((segundos % 3600) // 60)
    s = int(segundos % 60)
    ms = int((segundos - int(segundos)) * 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


@app.route("/gerar_descricao", methods=["POST"])
def gerar_descricao():
    global subpastas_por_slug

    data = request.get_json(force=True) or {}
    texto_original = data.get("texto_original")
    folder_id = data.get("folder_id")
    if not texto_original:
        return jsonify(error="campo 'texto_original' obrigatório"), 400

    slug = slugify(texto_original)
    descricao = f"Às vezes, tudo o que precisamos é lembrar disso: {texto_original.strip()}"

    txt_path = Path("saida") / f"{slug}_descricao.txt"
    txt_path.parent.mkdir(parents=True, exist_ok=True)
    txt_path.write_text(descricao, encoding="utf-8")

    try:
        drive = get_drive_service()
        base_folder = criar_pasta_se_preciso(GOOGLE_DRIVE_ROOT_FOLDER, drive)
        subfolder_id = subpastas_por_slug.get(slug) or criar_subpasta(slug, drive, base_folder)
        subpastas_por_slug[slug] = subfolder_id
        upload_para_drive(txt_path, txt_path.name, base_folder, drive)
        return jsonify(slug=slug, descricao=descricao, folder_url=f"https://drive.google.com/drive/folders/{subfolder_id}")
    except Exception as e:
        return jsonify(error="Erro ao enviar descrição", detalhe=str(e)), 500),
        slug=slug,
        drive_folder_url=f"https://drive.google.com/drive/folders/{subfolder_id}"
    )),
        slug=slug,
        drive_folder_url=f"https://drive.google.com/drive/folders/{subfolder_id}"
    )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)

