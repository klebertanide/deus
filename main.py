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


def criar_subpasta(nome: str, drive, parent_folder_id: str):
    try:
        results = drive.files().list(
            q=f"name='{nome}' and mimeType='application/vnd.google-apps.folder' and '{parent_folder_id}' in parents",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        items = results.get('files', [])
        if items:
            return items[0]['id']
    except Exception:
        pass
    meta = {
        "name": nome,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id]
    }
    return drive.files().create(body=meta).execute()["id"]


def upload_para_drive(path: Path, nome: str, folder_id: str, drive):
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(body={"name": nome, "parents": [folder_id]}, media_body=media).execute()


def gerar_slug():
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:6]


def slugify(text: str, limit: int = 30) -> str:
    txt = unidecode.unidecode(text or "")
    txt = re.sub(r"[^\w\s]", "", txt)
    txt = txt.strip().replace(" ", "_").lower()
    return txt[:limit] if txt else gerar_slug()


def elevenlabs_tts(text: str) -> bytes:
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    voice_id = "cwIsrQsWEVTols6slKYN"
    payload = {
        "text": text,
        "voice_settings": {"stability": 0.6, "similarity_boost": 0.9, "style": 0.15, "use_speaker_boost": True},
        "model_id": "eleven_multilingual_v2",
        "voice_id": voice_id
    }
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    for tentativa in range(2):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=60)
            r.raise_for_status()
            return r.content
        except Exception as e:
            if tentativa == 1:
                raise e


def parse_ts(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

# Health check endpoint
@app.route("/", methods=["GET"], strict_slashes=False)
def health_check():
    return jsonify(status="ok"), 200

# CSV generation endpoint
@app.route("/gerar_csv", methods=["GET", "POST"], strict_slashes=False)
def gerar_csv():
    if request.method == "GET":
        return jsonify(status="ready"), 200

    data = request.get_json(force=True) or {}
    transcricao = data.get("transcricao")
    texto_original = data.get("texto_original")
    slug = data.get("slug")
    aspect_ratio = data.get("aspect_ratio", "9:16")
    intervalo_segundos = data.get("intervalo_segundos", 3)

    if not transcricao:
        return jsonify(error="campo 'transcricao' obrigatório"), 400

    if not slug and not texto_original:
        slug = gerar_slug()
    elif not slug:
        slug = slugify(texto_original)

    drive = get_drive_service()
    pasta_id = criar_subpasta(slug, drive, GOOGLE_DRIVE_ROOT_FOLDER)
    csv_path = Path(f"{slug}_prompts.csv")

    # 1) gerar CSV file
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Prompt", "Visibility", "Aspect_ratio", "Magic_prompt", "Model",
                "Seed_number", "Rendering", "Negative_prompt", "Style", "color_palette", "Num_images"
            ])

            negative_prompt = (
                "words, sentences, texts, paragraphs, letters, numbers, syllables, "
                "low quality, overexposed, underexposed, extra limbs, extra fingers, missing fingers, "
                "disfigured, deformed, bad anatomy, realistic style, photographic style, 3d, 3d render"
            )

            duracao_total = max([blk[1] for blk in transcricao]) if transcricao else 0
            tempos_fixos = list(range(0, math.ceil(duracao_total), intervalo_segundos))

            prompts_com_tempo = []
            for inicio, fim, texto in transcricao:
                tempo = min(tempos_fixos, key=lambda t: abs(t - inicio))
                while tempo in [p[0] for p in prompts_com_tempo]:
                    tempo += intervalo_segundos
                    if tempo not in tempos_fixos:
                        tempos_fixos.append(tempo)
                prompts_com_tempo.append((tempo, texto))

            prompts_com_tempo.sort(key=lambda x: x[0])

            for tempo, texto in prompts_com_tempo:
                prompt_completo = (
                    f"{tempo}, {texto}, Delicate 2d watercolor painting with expressive brush strokes "
                    "and visible paper texture. Color palette blending soft pastels with bold hues. Artistic composition "
                    "that evokes emotion and depth, featuring flowing pigments, subtle gradients, and organic imperfections. "
                    "Emphasize the handcrafted feel, with layered translucency and a poetic atmosphere."
                )
                writer.writerow([
                    prompt_completo,
                    "private",
                    aspect_ratio,
                    "on",
                    "3",
                    "",
                    "turbo",
                    negative_prompt,
                    "design",
                    "",
                    "4"
                ])
    except Exception as e:
        app.logger.exception("Erro ao gerar CSV:")
        return jsonify(error="falha ao gerar CSV", detalhe=str(e)), 500

    # 2) upload para Drive
    try:
        upload_para_drive(csv_path, csv_path.name, pasta_id, drive)
    except Exception as e:
        app.logger.exception("Erro no upload para Drive:")
        return jsonify(error="falha no upload para Drive", detalhe=str(e)), 500

    # 3) sucesso
    return jsonify(
        slug=slug,
        folder_url=f"https://drive.google.com/drive/folders/{pasta_id}",
        intervalo_segundos=intervalo_segundos,
        num_prompts=len(prompts_com_tempo)
    ), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
