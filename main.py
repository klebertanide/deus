import os
import io
import csv
import re
import zipfile
import requests
import unidecode
import numpy as np
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import openai
from moviepy.editor import (
    AudioFileClip, ImageClip, TextClip,
    CompositeVideoClip, concatenate_videoclips,
    VideoFileClip
)
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# ——————————————— Tudo na raiz ———————————————
ROOT = Path(".")

GOOGLE_DRIVE_PARENT_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY         = os.getenv("ELEVENLABS_API_KEY")
openai.api_key         = os.getenv("OPENAI_API_KEY")

# ——————————————— Helpers ———————————————
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_pasta_drive(nome, drive):
    meta = {
        'name': nome,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [GOOGLE_DRIVE_PARENT_ID]
    }
    f = drive.files().create(body=meta, fields='id').execute()
    return f['id']

def upload_arquivo_drive(path: Path, nome: str, folder_id: str, drive):
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(
        body={'name': nome, 'parents': [folder_id]},
        media_body=media
    ).execute()

def slugify(text, limit=30):
    s = unidecode.unidecode(text)
    s = re.sub(r"[^\w\s]", "", s)
    return s.strip().replace(" ", "_").lower()[:limit]

def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def elevenlabs_tts(text):
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
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
        headers=headers, json=payload
    )
    r.raise_for_status()
    return r.content

# ——————————————— Rotas ———————————————
@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

@app.route("/audio/<fn>")
def serve_audio(fn):
    return send_from_directory(str(ROOT), fn)

# — POST /falar ⇒ gera <slug>.mp3 na raiz
@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug     = slugify(texto)
    mp3_path = ROOT / f"{slug}.mp3"

    try:
        audio = elevenlabs_tts(texto)
        mp3_path.write_bytes(audio)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    return jsonify(
        audio_url = request.url_root.rstrip("/") + f"/audio/{slug}.mp3",
        slug      = slug
    )

# — POST /transcrever ⇒ lê <slug>.mp3 e retorna transcrição
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    if audio_url.startswith(request.url_root.rstrip("/")):
        slug = audio_url.rsplit("/",1)[-1].removesuffix(".mp3")
        f    = open(ROOT / f"{slug}.mp3", "rb")
    else:
        resp = requests.get(audio_url, timeout=60); resp.raise_for_status()
        f    = io.BytesIO(resp.content); f.name = "audio.mp3"

    try:
        srt = openai.audio.transcriptions.create(
            model="whisper-1", file=f, response_format="srt"
        )
        def _parse(ts):
            h,m,rest = ts.split(":"); s,ms = rest.split(",")
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

        segs = []
        for blk in srt.strip().split("\n\n"):
            lines = blk.split("\n")
            if len(lines)<3: continue
            st,en = lines[1].split(" --> ")
            txt   = " ".join(lines[2:])
            segs.append({"inicio":_parse(st),"fim":_parse(en),"texto":txt})

        return jsonify(duracao_total=segs[-1]["fim"], transcricao=segs)
    finally:
        try: f.close()
        except: pass

# — POST /gerar_csv ⇒ cria .csv, .srt, .txt na raiz e faz upload
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data        = request.get_json() or {}
    transcr     = data.get("transcricao", [])
    prompts     = data.get("prompts", [])
    descricao   = data.get("descricao","")
    orig_text   = data.get("texto_original","")
    if not transcr or not prompts or len(transcr)!=len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    slug     = slugify(orig_text or descricao)
    drive    = get_drive_service()
    folderID = criar_pasta_drive(slug, drive)

    csv_pth = ROOT / f"{slug}.csv"
    srt_pth = ROOT / f"{slug}.srt"
    txt_pth = ROOT / f"{slug}.txt"

    # CSV
    with open(csv_pth, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["TIME","PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT",
                    "MODEL","SEED","RENDERING","NEGATIVE","STYLE","PALETTE"])
        neg = "low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy"
        for seg,p in zip(transcr,prompts):
            t = int(seg["inicio"])
            w.writerow([t, f"{t} - {p}", "PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT
    with open(srt_pth, "w", encoding="utf-8") as f:
        for i,seg in enumerate(transcr,1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")

    # TXT
    txt_pth.write_text(descricao.strip(), encoding="utf-8")

    # Upload
    upload_arquivo_drive(csv_pth, csv_pth.name, folderID, drive)
    upload_arquivo_drive(srt_pth, srt_pth.name, folderID, drive)
    upload_arquivo_drive(txt_pth, txt_pth.name, folderID, drive)
    mp3_pth = ROOT / f"{slug}.mp3"
    if mp3_pth.exists():
        upload_arquivo_drive(mp3_pth, mp3_pth.name, folderID, drive)

    return jsonify(slug=slug, folder_url=f"https://drive.google.com/drive/folders/{folderID}")

# — POST /upload_zip ⇒ recebe ZIP, extrai e escolhe imagens na raiz
@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    z = request.files.get("zip")
    if not z:
        return jsonify(error="campo 'zip' obrigatório"), 400

    slug = request.form.get("slug")
    if not slug:
        return jsonify(error="campo 'slug' obrigatório"), 400

    tmp_dir = ROOT / f"{slug}_tmp"
    out_dir = ROOT / slug
    for d in (tmp_dir, out_dir):
        if d.exists(): 
            for f in d.iterdir(): f.unlink()
        else:
            d.mkdir()

    zip_path = tmp_dir / "imgs.zip"
    z.save(zip_path)
    with zipfile.ZipFile(zip_path) as zz:
        zz.extractall(tmp_dir)

    imgs = [p for p in tmp_dir.iterdir() if p.suffix.lower() in (".jpg"," .png"," .jpeg")]
    if not imgs:
        return jsonify(error="nenhuma imagem no zip"), 400

    # lê prompts
    slug_csv = ROOT / f"{slug}.csv"
    prompts  = []
    with open(slug_csv, encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for r in rd:
            prompts.append(r["PROMPT"].split(" - ",1)[-1])

    selected = []
    for i,_ in enumerate(prompts):
        src = imgs[0]
        dst = out_dir / f"{i:02d}_{src.name}"
        src.rename(dst)
        selected.append(dst.name)

    return jsonify(ok=True, slug=slug, imagens=selected)

# — POST /montar_video ⇒ monta MP4 final na raiz
@app.route("/montar_video", methods=["POST"])
def montar_video():
    data     = request.get_json() or {}
    slug     = data.get("slug")
    folderID = data.get("folder_id")
    if not slug or not folderID:
        return jsonify(error="slug e folder_id obrigatórios"), 400

    # lê SRT para segmentos
    srt_pth  = ROOT / f"{slug}.srt"
    with open(srt_pth, encoding="utf-8") as f:
        blocks = [b for b in f.read().split("\n\n") if b.strip()]
    segs = []
    for blk in blocks:
        l = blk.split("\n")
        if len(l)>=3:
            text = " ".join(l[2:])
            start = None; end = None
            segs.append({"texto":text})

    # áudio
    mp3_pth = ROOT / f"{slug}.mp3"
    audio   = AudioFileClip(str(mp3_pth))

    # clipes de imagem + texto
    clips = []
    imgs  = sorted((ROOT/slug).iterdir())
    for i,seg in enumerate(segs):
        dur     = 3
        img_clip= ImageClip(str(imgs[i % len(imgs)]))\
            .resize(height=720).crop(width=1280, x_center="center")\
            .set_duration(dur)
        txt_clip= TextClip(seg["texto"], fontsize=50, color="white",
                           stroke_color="black", stroke_width=2,
                           method="caption")\
            .set_duration(dur).set_position(("center","bottom"))
        clips.append(CompositeVideoClip([img_clip, txt_clip], size=(1280,720)))

    base = concatenate_videoclips(clips).set_audio(audio)
    total_dur = base.duration

    # efeitos e watermark
    fx_layer = VideoFileClip("sobrepor.mp4")\
        .resize((1280,720)).set_opacity(0.2)\
        .set_duration(total_dur+3)
    wm       = ImageClip("sobrepor.png")\
        .resize((1280,720)).set_duration(total_dur)

    # fechamento
    close = ImageClip("fechamento.png")\
        .resize((1280,720)).set_duration(3)

    main_cmp = CompositeVideoClip([base, fx_layer.subclip(0,total_dur), wm],
                                  size=(1280,720))
    close_cmp= CompositeVideoClip([close, fx_layer.subclip(total_dur, total_dur+3)],
                                  size=(1280,720))

    final = concatenate_videoclips([main_cmp, close_cmp])\
        .set_audio(audio)
    outp  = ROOT / f"{slug}.mp4"
    final.write_videofile(str(outp), fps=24, codec="libx264", audio_codec="aac")

    # upload
    drive = get_drive_service()
    upload_arquivo_drive(outp, outp.name, folderID, drive)

    return jsonify(video_url=f"https://drive.google.com/drive/folders/{folderID}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)
