import os
import io
import csv
import re
import uuid
import zipfile
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
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# ——————————————— Configurações gerais ———————————————
BASE = Path(".")
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
openai.api_key = os.getenv("OPENAI_API_KEY")
GOOGLE_DRIVE_PARENT_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(text, limit=30):
    txt = unidecode.unidecode(text)
    txt = re.sub(r"[^\w\s]", "", txt)
    return txt.strip().replace(" ", "_").lower()[:limit]

def upload_to_drive(path: Path, name: str, folder_id: str, drive):
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(
        body={"name": name, "parents": [folder_id]},
        media_body=media
    ).execute()

def create_drive_folder(name: str, drive) -> str:
    meta = {
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [GOOGLE_DRIVE_PARENT_ID]
    }
    folder = drive.files().create(body=meta, fields="id").execute()
    return folder["id"]

# ——————————————— Endpoints públicos ———————————————

@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

@app.route("/downloads/<slug>/<path:fn>")
def serve_file(slug, fn):
    return send_from_directory(BASE / slug, fn)

@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug     = slugify(texto)
    slug_dir = BASE / slug
    slug_dir.mkdir(exist_ok=True)

    mp3_path = slug_dir / f"{slug}.mp3"
    # ===== ElevenLabs TTS =====
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": texto,
        "voice_settings": {"stability":0.6,"similarity_boost":0.9,"style":0.15,"use_speaker_boost":True},
        "model_id": "eleven_multilingual_v2",
        "voice_id":  "cwIsrQsWEVTols6slKYN"
    }
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
        headers=headers, json=payload
    )
    r.raise_for_status()
    mp3_path.write_bytes(r.content)

    return jsonify(
        slug      = slug,
        audio_url = f"{request.url_root.rstrip('/')}/downloads/{slug}/{slug}.mp3"
    )

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    # carrega local ou remoto
    if audio_url.startswith(request.url_root.rstrip("/")):
        _, slug, filename = audio_url.rsplit("/", 2)
        fileobj = open(BASE/slug/filename, "rb")
    else:
        resp = requests.get(audio_url, timeout=60); resp.raise_for_status()
        fileobj = io.BytesIO(resp.content); fileobj.name = audio_url.split("/")[-1]

    try:
        srt = openai.audio.transcriptions.create(
            model="whisper-1",
            file=fileobj,
            response_format="srt"
        )
        def p2s(ts):
            h,m,rest = ts.split(":"); s,ms = rest.split(",")
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

        segs = []
        for blk in srt.strip().split("\n\n"):
            lines = blk.split("\n")
            if len(lines)<3: continue
            st,en = lines[1].split(" --> ")
            txt   = " ".join(lines[2:])
            segs.append({"inicio": p2s(st), "fim": p2s(en), "texto": txt})

        return jsonify(duracao_total=segs[-1]["fim"], transcricao=segs)
    finally:
        try: fileobj.close()
        except: pass

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data           = request.get_json() or {}
    transcricao    = data.get("transcricao", [])
    prompts        = data.get("prompts", [])
    descricao      = data.get("descricao", "")
    texto_original = data.get("texto_original", "")

    slug = slugify(texto_original or descricao)
    slug_dir = BASE / slug
    slug_dir.mkdir(exist_ok=True)

    if not transcricao or not prompts or len(transcricao)!=len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    drive     = get_drive_service()
    folder_id = create_drive_folder(slug, drive)

    # CSV
    csv_path = slug_dir / f"{slug}.csv"
    neg = "low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["TIME","PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL","SEED","RENDERING","NEGATIVE","STYLE","PALETTE"])
        for seg,p in zip(transcricao,prompts):
            t = int(seg["inicio"])
            w.writerow([t, f"{t} - {p}", "PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT
    def fmt(s):
        ms = int((s%1)*1000); h=int(s//3600); m=int((s%3600)//60); sec=int(s%60)
        return f"{h:02}:{m:02}:{sec:02},{ms:03}"
    srt_path = slug_dir / f"{slug}.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        for i,seg in enumerate(transcricao,1):
            f.write(f"{i}\n{fmt(seg['inicio'])} --> {fmt(seg['fim'])}\n{seg['texto']}\n\n")

    # TXT com a descrição
    txt_path = slug_dir / f"{slug}.txt"
    txt_path.write_text(descricao.strip(), encoding="utf-8")

    # envia tudo ao Drive
    for p in (csv_path, srt_path, txt_path, slug_dir / f"{slug}.mp3"):
        if p.exists():
            upload_to_drive(p, p.name, folder_id, drive)
    return jsonify(slug=slug, folder_url=f"https://drive.google.com/drive/folders/{folder_id}")

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    slug = request.form.get("slug")
    if not slug:
        return jsonify(error="slug obrigatório no form-data"), 400
    slug_dir = BASE / slug
    if not slug_dir.exists():
        return jsonify(error="slug inválido"), 400

    file = request.files.get("zip")
    if not file:
        return jsonify(error="zip obrigatório"), 400

    imgs_dir = slug_dir / "images"
    imgs_dir.mkdir(exist_ok=True)
    zpath = imgs_dir / "raw.zip"
    file.save(zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(imgs_dir)

    imgs = sorted(imgs_dir.glob("*.[jp][pn]g"))
    if not imgs:
        return jsonify(error="nenhuma imagem encontrada"), 400

    # placeholder simples: pega a primeira de cada
    selecionadas = []
    out = imgs_dir / "selected"
    out.mkdir(exist_ok=True)
    for idx, img in enumerate(imgs):
        dst = out / f"{idx:02d}_{img.name}"
        img.rename(dst)
        selecionadas.append(dst.name)

    return jsonify(ok=True, slug=slug, imagens=selecionadas)

@app.route("/montar_video", methods=["POST"])
def montar_video():
    data      = request.get_json() or {}
    slug      = data.get("slug")
    folder_id = data.get("folder_id")
    if not slug or not folder_id:
        return jsonify(error="slug e folder_id obrigatórios"), 400

    slug_dir = BASE / slug
    imgs = sorted((slug_dir/"images"/"selected").glob("*.[jp][pn]g"))
    if not imgs:
        return jsonify(error="sem imagens"), 400

    # áudio e transcrição já estão em slug_dir
    audio_clip = AudioFileClip(str(slug_dir/f"{slug}.mp3"))
    # parse SRT
    segmentos = []
    for blk in (slug_dir/f"{slug}.srt").read_text(encoding="utf-8").strip().split("\n\n"):
        l = blk.split("\n")
        if len(l)>=3:
            h1,m1,rest1 = l[1].split(" --> ")[0].split(":"); s1,ms1 = rest1.split(",")
            h2,m2,rest2 = l[1].split(" --> ")[1].split(":"); s2,ms2 = rest2.split(",")
            t0 = int(h1)*3600+int(m1)*60+int(s1)+int(ms1)/1000
            t1 = int(h2)*3600+int(m2)*60+int(s2)+int(ms2)/1000
            segmentos.append({"inicio":t0,"fim":t1,"texto":l[2]})

    clips = []
    for idx,seg in enumerate(segmentos):
        dur = seg["fim"]-seg["inicio"]
        img_clip = ImageClip(str(imgs[idx%len(imgs)])).resize(height=720).crop(x_center="center", width=1280).set_duration(dur)
        zoom     = img_clip.resize(lambda t:1+0.02*t)
        txt      = TextClip(seg["texto"], fontsize=60, color="white", stroke_color="black", stroke_width=2, method="caption")\
                       .set_duration(dur).set_position(("center","bottom"))
        clips.append(CompositeVideoClip([zoom, txt], size=(1280,720)))

    body = concatenate_videoclips(clips).set_audio(audio_clip)

    # ——— importa overlays (espera na raiz) ———
    overlay      = VideoFileClip("sobrepor.mp4").without_audio()\
                       .resize(body.size).set_opacity(0.2).set_duration(body.duration)
    watermark    = ImageClip("sobrepor.png").resize(body.size)\
                       .set_opacity(0.2).set_duration(body.duration - 3)
    closing_img  = ImageClip("fechamento.png").resize(body.size).set_duration(3)
    # body + overlay + watermark
    video_body   = CompositeVideoClip([body, overlay, watermark], size=body.size)
    # fechamento com só overlay
    overlay_sub  = overlay.subclip(0, 3)
    video_close  = CompositeVideoClip([closing_img, overlay_sub], size=body.size).set_duration(3)
    final        = concatenate_videoclips([video_body, video_close]).set_audio(audio_clip)

    out_path = slug_dir / f"{slug}.mp4"
    final.write_videofile(str(out_path), fps=24, codec="libx264", audio_codec="aac")

    # envia ao Drive
    drive = get_drive_service()
    upload_to_drive(out_path, f"{slug}.mp4", folder_id, drive)

    return jsonify(video_url=f"https://drive.google.com/drive/folders/{folder_id}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
