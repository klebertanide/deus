import os
import io
import csv
import re
import uuid
import zipfile
import tempfile
import requests
import unidecode
from pathlib import Path
from flask import Flask, request, jsonify, send_file
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

GOOGLE_DRIVE_ROOT_FOLDER = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
SERVICE_ACCOUNT_FILE = "/etc/secrets/service_account.json"
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
openai.api_key = os.getenv("OPENAI_API_KEY")


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
        body={"name": nome, "parents": [folder_id]},
        media_body=media
    ).execute()

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
        "voice_id": "cwIsrQsWEVTols6slKYN"
    }
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
        headers=headers,
        json=payload
    )
    r.raise_for_status()
    return r.content

@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json(force=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug = slugify(texto)
    mp3_path = Path(f"{slug}.mp3")

    try:
        audio_bytes = elevenlabs_tts(texto)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    mp3_path.write_bytes(audio_bytes)

    # use sempre "audio_url", apontando para o arquivo local
    return jsonify(
        audio_url=str(mp3_path.resolve()),  # caminho absoluto
        slug=slug
    )

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True) or {}
    audio_ref = data.get("audio_url")
    if not audio_ref:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    # Tenta abrir localmente; se não existir, faz GET na URL
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
        # Gera SRT via Whisper
        srt = openai.audio.transcriptions.create(
            model="whisper-1",
            file=fobj,
            response_format="srt"
        )

        def parse_ts(ts: str) -> float:
            h, m, rest = ts.split(":")
            s, ms      = rest.split(",")
            return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

        segmentos = []
        for bloco in srt.strip().split("\n\n"):
            lines = bloco.split("\n")
            if len(lines) < 3:
                continue
            inicio_ts, fim_ts = lines[1].split(" --> ")
            texto_seg = " ".join(lines[2:])
            segmentos.append({
                "inicio": parse_ts(inicio_ts),
                "fim":    parse_ts(fim_ts),
                "texto":  texto_seg
            })

        return jsonify(transcricao=segmentos)

    except Exception as e:
        return jsonify(error="falha na transcrição", detalhe=str(e)), 500

    finally:
        try:
            fobj.close()
        except:
            pass

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json() or {}
    transcricao = data.get("transcricao", [])
    prompts     = data.get("prompts", [])
    descricao   = data.get("descricao", "")
    texto_orig  = data.get("texto_original", "")
    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    slug = slugify(texto_orig or descricao)
    drive = get_drive_service()
    folder_id = criar_pasta_drive(slug, drive)

    # CSV
    csv_path = Path(f"{slug}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "TIME","PROMPT","VISIBILITY","ASPECT_RATIO",
            "MAGIC_PROMPT","MODEL","SEED","RENDERING",
            "NEGATIVE","STYLE","PALETTE"
        ])
        neg = "low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy"
        for seg, p in zip(transcricao, prompts):
            t = int(seg["inicio"])
            w.writerow([t, f"{t} - {p}", "PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT
    def fmt(s):
        ms = int((s%1)*1000); h=int(s//3600); m=int((s%3600)//60); sec=int(s%60)
        return f"{h:02}:{m:02}:{sec:02},{ms:03}"
    srt_path = Path(f"{slug}.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(transcricao, 1):
            f.write(f"{i}\n{fmt(seg['inicio'])} --> {fmt(seg['fim'])}\n{seg['texto']}\n\n")

    for p in (csv_path, srt_path, Path(f"{slug}.mp3")):
        if p.exists():
            upload_para_drive(p, p.name, folder_id, drive)

    return jsonify(slug=slug, folder_url=f"https://drive.google.com/drive/folders/{folder_id}")

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    slug = request.form.get("slug")
    if not file or not slug:
        return jsonify(error="zip e slug obrigatórios."), 400

    zip_path = Path(f"{slug}.zip")
    file.save(zip_path)

    with tempfile.TemporaryDirectory() as tmp:
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(tmp)
        imgs = [Path(tmp)/f for f in os.listdir(tmp) if f.lower().endswith((".jpg",".png",".jpeg"))]
        if not imgs:
            return jsonify(error="nenhuma imagem no zip"), 400

        # lê prompts do CSV
        csv_path = Path(f"{slug}.csv")
        prompts = []
        with open(csv_path, encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for r in rd:
                prompts.append(r["PROMPT"].split(" - ",1)[-1])

        selecionadas = []
        for idx, prompt in enumerate(prompts):
            img = imgs[idx % len(imgs)]
            nome = f"{slug}_{idx}_{img.name}"
            dst = Path(nome)
            img.rename(dst)
            selecionadas.append(nome)

    return jsonify(ok=True, usadas=selecionadas)

@app.route("/montar_video", methods=["POST"])
def montar_video():
    data = request.get_json() or {}
    slug      = data.get("slug")
    folder_id = data.get("folder_id")
    if not slug or not folder_id:
        return jsonify(error="slug e folder_id obrigatórios"), 400

    imgs = sorted([f for f in os.listdir() if f.startswith(f"{slug}_") and f.lower().endswith((".jpg",".png"))])
    if not imgs:
        return jsonify(error="sem imagens selecionadas"), 400

    audio_path = Path(f"{slug}.mp3")
    if not audio_path.exists():
        return jsonify(error="áudio não encontrado"), 400
    audio = AudioFileClip(str(audio_path))

    srt_path = Path(f"{slug}.srt")
    segs = []
    with open(srt_path, encoding="utf-8") as f:
        for blk in f.read().strip().split("\n\n"):
            lines = blk.split("\n")
            if len(lines) >= 3:
                st, en = lines[1].split(" --> ")
                segs.append({"inicio": 0, "fim": 3, "texto": lines[2]})

    clips = []
    for idx, seg in enumerate(segs):
        dur = seg["fim"] - seg["inicio"]
        img_clip = ImageClip(imgs[idx % len(imgs)]).resize(height=720).crop(x_center="center", width=1280).set_duration(dur)
        zoom = img_clip.resize(lambda t: 1+0.02*t)
        txt = TextClip(seg["texto"], fontsize=60, color="white", stroke_color="black", stroke_width=2, method="caption")\
              .set_duration(dur).set_position(("center", "bottom"))
        comp = CompositeVideoClip([zoom, txt], size=(1280, 720))
        clips.append(comp)

    base = concatenate_videoclips(clips).set_audio(audio)
    total_dur = base.duration

    overlay = VideoFileClip("sobrepor.mp4").resize((1280,720)).set_opacity(0.2).set_duration(total_dur)
    closing_dur = 3
    watermark = ImageClip("sobrepor.png").set_duration(total_dur - closing_dur).set_position(("center","center"))
    closing = ImageClip("fechamento.png").set_duration(closing_dur).set_start(total_dur - closing_dur).set_position(("center","center"))

    final = CompositeVideoClip([base, overlay, watermark, closing], size=(1280,720))
    outp = Path(f"{slug}.mp4")
    final.write_videofile(str(outp), fps=24, codec="libx264", audio_codec="aac")

    drive = get_drive_service()
    upload_para_drive(outp, outp.name, folder_id, drive)
    return jsonify(video_url=f"https://drive.google.com/drive/folders/{folder_id}")

@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)
