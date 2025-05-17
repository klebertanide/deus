import os
import io
import csv
import re
import zipfile
import tempfile
import requests
import unidecode
from pathlib import Path
from flask import Flask, request, jsonify
import openai
from moviepy.editor import (
    AudioFileClip,
    ImageClip,
    TextClip,
    CompositeVideoClip,
    concatenate_videoclips,
    VideoFileClip
)
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# —————— Configuração Google Drive ——————
GOOGLE_DRIVE_ROOT_FOLDER = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
SERVICE_ACCOUNT_FILE     = "/etc/secrets/service_account.json"
ELEVEN_API_KEY           = os.getenv("ELEVENLABS_API_KEY")
openai.api_key           = os.getenv("OPENAI_API_KEY")

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_pasta_drive(nome, drive):
    meta = {
        "name": nome,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [GOOGLE_DRIVE_ROOT_FOLDER]
    }
    fld = drive.files().create(body=meta, fields="id").execute()
    return fld["id"]

def upload_para_drive(path: Path, nome: str, folder_id: str, drive):
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(
        body={"name": nome, "parents":[folder_id]},
        media_body=media
    ).execute()

# —————— Helpers ——————
def slugify(text: str, limit: int = 30) -> str:
    txt = unidecode.unidecode(text)
    txt = re.sub(r"[^\w\s]", "", txt)
    return txt.strip().replace(" ", "_").lower()[:limit]

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
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
        headers=headers,
        json=payload
    )
    r.raise_for_status()
    return r.content

def parse_ts(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms      = rest.split(",")
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

# —————— Rotas ——————
@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json(force=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug     = slugify(texto)
    mp3_path = Path(f"{slug}.mp3")

    try:
        audio_bytes = elevenlabs_tts(texto)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    mp3_path.write_bytes(audio_bytes)
    return jsonify(
        audio_url=str(mp3_path.resolve()),
        slug=slug
    )

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json(force=True) or {}

    # aceita tanto audio_url (novo) quanto audio_file (antigo)
    audio_ref = data.get("audio_url") or data.get("audio_file")
    if not audio_ref:
        return jsonify(error="campo 'audio_url' ou 'audio_file' obrigatório"), 400

    # tenta abrir localmente; se não existir, faz GET na URL
    try:
        if os.path.exists(audio_ref):
            fobj = open(audio_ref, "rb")
        else:
            resp = requests.get(audio_ref, timeout=60)
            resp.raise_for_status()
            fobj = io.BytesIO(resp.content)
            fobj.name = Path(audio_ref).name
    except Exception as e:
        return jsonify(error="falha ao carregar áudio", detalhe=str(e)), 400

    try:
        srt = openai.audio.transcriptions.create(
            model="whisper-1",
            file=fobj,
            response_format="srt"
        )
        segmentos = []
        for bloco in srt.strip().split("\n\n"):
            lines = bloco.split("\n")
            if len(lines) < 3: continue
            st, en  = lines[1].split(" --> ")
            txt_seg = " ".join(lines[2:])
            segmentos.append({
                "inicio": parse_ts(st),
                "fim":    parse_ts(en),
                "texto":  txt_seg
            })

        total = segmentos[-1]["fim"] if segmentos else 0
        return jsonify(transcricao=segmentos, duracao_total=total)

    except Exception as e:
        return jsonify(error="falha na transcrição", detalhe=str(e)), 500

    finally:
        try: fobj.close()
        except: pass

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data        = request.get_json() or {}
    transcricao = data.get("transcricao", [])
    prompts     = data.get("prompts", [])
    texto_orig  = data.get("texto_original", "")
    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    slug  = slugify(texto_orig)
    drive = get_drive_service()
    slug      = slugify(texto_orig)
    drive     = get_drive_service()
    folder_id = criar_pasta_drive(slug, drive)

    # -------- gera descrição do vídeo via OpenAI --------
    # ——— Gera descrição via OpenAI ———
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4",
@@ -187,15 +187,15 @@
        )
        descricao = resp.choices[0].message.content.strip()
    except Exception:
        descricao = ""  # não crítico: prossegue sem descrição
        descricao = ""

    # salva em arquivo .txt também
    # ——— Salva e envia .txt ———
    txt_path = Path(f"{slug}.txt")
    if descricao:
        txt_path.write_text(descricao, encoding="utf-8")
        upload_para_drive(txt_path, txt_path.name, folder_id, drive)

    # -------- CSV --------
    # ——— Gera e envia CSV ———
    csv_path = Path(f"{slug}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
@@ -207,10 +207,11 @@
        neg = "low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy, realistic style, photographic style, text"
        for seg, p in zip(transcricao, prompts):
            t = int(seg["inicio"])
            w.writerow([f"{t} - {p} - Rendered in vibrant watercolor style with visible brushstroke textures, layered pigment, and wet-on-wet blending effects. Edges of the paint bleed naturally, with expressive strokes and color blooms that emphasize the handcrafted, painterly feel of traditional watercolor illustrations.", "PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])
            prompt_full = f"{t} - {p} - Rendered in vibrant watercolor style with visible brushstroke textures..."
            w.writerow([prompt_full, "PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])
    upload_para_drive(csv_path, csv_path.name, folder_id, drive)

    # -------- SRT --------
    # ——— Gera e envia SRT ———
    def fmt(s):
        ms = int((s%1)*1000); h=int(s//3600); m=int((s%3600)//60); sec=int(s%60)
        return f"{h:02}:{m:02}:{sec:02},{ms:03}"
@@ -220,7 +221,7 @@
            f.write(f"{i}\n{fmt(seg['inicio'])} --> {fmt(seg['fim'])}\n{seg['texto']}\n\n")
    upload_para_drive(srt_path, srt_path.name, folder_id, drive)

    # também assegura envio do MP3
    # ——— Envia também o MP3 ———
    mp3 = Path(f"{slug}.mp3")
    if mp3.exists():
        upload_para_drive(mp3, mp3.name, folder_id, drive)
@@ -233,38 +234,34 @@

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    slug = request.form.get("slug")
    if not file or not slug:
        return jsonify(error="zip e slug obrigatórios."), 400
    file      = request.files.get("zip")
    slug      = request.form.get("slug")
    folder_id = request.form.get("folder_id")
    if not file or not slug or not folder_id:
        return jsonify(error="zip, slug e folder_id obrigatórios"), 400

    zip_path = Path(f"{slug}.zip")
    file.save(zip_path)

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        imgs = [Path(tmp)/f for f in os.listdir(tmp)
                if f.lower().endswith((".jpg",".png",".jpeg"))]
        imgs = sorted(
            [p for p in Path(tmp).iterdir() if p.suffix.lower() in (".jpg",".png",".jpeg")],
            key=lambda p: p.stat().st_mtime
        )
        if not imgs:
            return jsonify(error="nenhuma imagem no zip"), 400

        # lê prompts do CSV
        csv_path = Path(f"{slug}.csv")
        prompts  = []
        with open(csv_path, encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for r in rd:
                prompts.append(r["PROMPT"].split(" - ",1)[-1])

        usadas = []
        for idx, _ in enumerate(prompts):
            img = imgs[idx % len(imgs)]
            dst = Path(f"{slug}_{idx}_{img.name}")
            img.rename(dst)
            usadas.append(str(dst))
        drive = get_drive_service()
        imagens_info = []
        for img_path in imgs:
            t = int(img_path.stat().st_mtime)
            nome_novo = f"{t}.jpg"
            upload_para_drive(img_path, nome_novo, folder_id, drive)
            imagens_info.append(nome_novo)

    return jsonify(slug=slug, images=usadas)
    return jsonify(slug=slug, images=imagens_info)

@app.route("/montar_video", methods=["POST"])
def montar_video():
@@ -274,8 +271,25 @@
    if not slug or not folder_id:
        return jsonify(error="slug e folder_id obrigatórios"), 400

    imgs = sorted(f for f in os.listdir()
                  if f.startswith(f"{slug}_") and f.lower().endswith((".jpg",".png")))
    # ——— Baixa imagens JPG da pasta no Drive ———
    drive = get_drive_service()
    resp = drive.files().list(q=f"'{folder_id}' in parents", fields="files(id,name)").execute()
    for fmeta in resp.get("files", []):
        name = fmeta["name"]
        if name.lower().endswith(".jpg"):
            req = drive.files().get_media(fileId=fmeta["id"])
            with open(name, "wb") as f:
                from googleapiclient.http import MediaIoBaseDownload
                downloader = MediaIoBaseDownload(f, req)
                done = False
                while not done:
                    _, done = downloader.next_chunk()

    # ——— Lista imagens locais ordenadas pelo nome “TIMESTAMP.jpg” ———
    imgs = sorted(
        [f for f in os.listdir() if f.endswith(".jpg") and f.split(".")[0].isdigit()],
        key=lambda x: int(Path(x).stem)
    )
    if not imgs:
        return jsonify(error="sem imagens selecionadas"), 400
