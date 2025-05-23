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
    except:
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
        r = requests.post(
            "https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
            headers=headers,
            json=payload,
            timeout=60
        )
        try:
            r.raise_for_status()
            return r.content
        except:
            if tentativa == 1:
                raise

def parse_ts(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

# ---------- ROTA DE HEALTH-CHECK ----------
@app.route("/", methods=["GET", "HEAD"])
def index():
    """Retorna 200 para indicar que o serviço está online."""
    return jsonify(status="online", message="API do gerador de CSV ativa"), 200
# -----------------------------------------

@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json(force=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug = slugify(texto)
    mp3_path = Path(f"{slug}_audio.mp3")
    txt_path = Path(f"{slug}_texto.txt")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(texto)

    audio_bytes = elevenlabs_tts(texto)
    if not audio_bytes or len(audio_bytes) < 1000:
        return jsonify(error="Áudio gerado inválido"), 500
    mp3_path.write_bytes(audio_bytes)

    drive = get_drive_service()
    folder_id = criar_subpasta(slug, drive, GOOGLE_DRIVE_ROOT_FOLDER)
    upload_para_drive(mp3_path, mp3_path.name, folder_id, drive)
    upload_para_drive(txt_path, txt_path.name, folder_id, drive)

    return jsonify(audio_url=str(mp3_path.resolve()), slug=slug, folder_id=folder_id)

# ... [IMPORTS E CONFIGURAÇÕES INALTERADOS ACIMA] ...

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True) or {}
    audio_ref = data.get("audio_url") or data.get("audio_file")
    slug = data.get("slug")

    if not audio_ref and slug:
        # fallback: tenta achar o arquivo local
        fallback_path = Path(f"{slug}_audio.mp3")
        if fallback_path.exists():
            audio_ref = str(fallback_path)

    if not audio_ref:
        return jsonify(error="audio_url ou audio_file obrigatório"), 400

    if not slug:
        slug = Path(audio_ref).stem.replace("_audio", "")

    if os.path.exists(audio_ref):
        fobj = open(audio_ref, "rb")
    else:
        resp = requests.get(audio_ref, timeout=60); resp.raise_for_status()
        fobj = io.BytesIO(resp.content); fobj.name = Path(audio_ref).name
        resp = requests.get(audio_ref, timeout=60)
        resp.raise_for_status()
        fobj = io.BytesIO(resp.content)
        fobj.name = Path(audio_ref).name

    raw_srt = client.audio.transcriptions.create(
        model="whisper-1", file=fobj, response_format="srt"
    )
    blocks = []
    for blk in raw_srt.strip().split("\n\n"):
        parts = blk.split("\n")
        if len(parts) < 3:
            continue
        st_ts, en_ts = parts[1].split(" --> ")
        txt = " ".join(parts[2:])
        inicio, fim = parse_ts(st_ts), parse_ts(en_ts)
        blocks.append({"inicio": inicio, "fim": fim, "texto": txt})
    total = blocks[-1]["fim"] if blocks else 0

    srt_path = Path(f"{slug}_legenda.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(raw_srt)

    try:
        drive = get_drive_service()
        folder_id = criar_subpasta(slug, drive, GOOGLE_DRIVE_ROOT_FOLDER)
        upload_para_drive(srt_path, srt_path.name, folder_id, drive)
    except:
        pass

    fobj.close()
    return jsonify(transcricao=blocks, duracao_total=total, slug=slug)

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json(force=True) or {}
    transcricao         = data.get("transcricao")
    prompts             = data.get("prompts")
    slug                = data.get("slug")
    aspect_ratio        = data.get("aspect_ratio", "9:16")
    intervalo_segundos  = data.get("intervalo_segundos", 4)

    if not transcricao or not prompts:
        return jsonify(error="transcricao e prompts são obrigatórios"), 400

    # Correção automática com aviso
    if len(prompts) != len(transcricao):
        # <-- AGORA O AVISO APARECE ANTES DO RETURN -->
        print(f"[WARN] Quantidade de prompts ({len(prompts)}) diferente da transcrição ({len(transcricao)})")
        return jsonify(
            error="número de prompts deve ser igual ao número de blocos de transcrição"
        ), 400

    if not slug:
        slug = gerar_slug()

    try:
        drive    = get_drive_service()
        pasta_id = criar_subpasta(slug, drive, GOOGLE_DRIVE_ROOT_FOLDER)

        duracao_total = max(b["fim"] for b in transcricao)

        # Arredonda cada início para baixo ao múltiplo de intervalo
        init_times = [
            math.floor(b["inicio"] / intervalo_segundos) * intervalo_segundos
            for b in transcricao
        ]

        # Garante sequência crescente e dentro do total
        ordered_times = []
        prev_time = -intervalo_segundos
        for t in init_times:
            t_ok = max(t, prev_time + intervalo_segundos)
            max_allowed = max(duracao_total - intervalo_segundos, 0)
            t_ok = min(t_ok, max_allowed)
            ordered_times.append(t_ok)
            prev_time = t_ok

        # Zipa e ordena por tempo
        prompts_com_tempo = sorted(
            zip(ordered_times, prompts, transcricao),
            key=lambda x: x[0]
        )

        csv_path = Path(f"{slug}_prompts.csv")
        negative_prompt = (
            "words, sentences, texts, paragraphs, letters, numbers, syllables, "
            "low quality, overexposed, underexposed, extra limbs, extra fingers, "
            "missing fingers, disfigured, deformed, bad anatomy, realistic style, "
            "photographic style, 3d, 3d render"
        )

        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "Prompt", "Visibility", "Aspect_ratio", "Magic_prompt", "Model",
                "Seed_number", "Rendering", "Negative_prompt", "Style",
                "color_palette", "Num_images"
            ])

            for tempo, prompt_texto, bloco in prompts_com_tempo:
                prompt_completo = (
                    f"{int(tempo)}, {prompt_texto}, "
                    "Delicate 2D watercolor painting with expressive brushstrokes"
                    "and visible paper texture. Grain effect. Color palette that mixes soft pastel tones with darker, more depressive hues. Artistic composition that evokes "
                    "emotion and depth, with fluid pigments, subtle gradients and organic imperfections. Emphasizes the artisanal touch, with layered translucency and a poetic, nihilistic atmosphere."
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

        upload_para_drive(csv_path, csv_path.name, pasta_id, drive)
        return jsonify(
            slug=slug,
            folder_url=f"https://drive.google.com/drive/folders/{pasta_id}",
            intervalo_segundos=intervalo_segundos,
            num_prompts=len(prompts_com_tempo)
        )

    except Exception as e:
        return jsonify(error="falha ao gerar CSV ou fazer upload", detalhe=str(e)), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
