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

# Google Drive
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# Pastas locais
BASE       = Path(".")
AUDIO_DIR  = BASE / "audio"
CSV_DIR    = BASE / "csv"
FILES_DIR  = BASE / "downloads"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Configurações
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2")
ELEVEN_API_KEY        = os.getenv("ELEVENLABS_API_KEY")
openai.api_key        = os.getenv("OPENAI_API_KEY")

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(texto: str, limite: int = 30) -> str:
    # Pega os primeiros `limite` caracteres, remove acentos e pontuação, e formata
    snippet = texto.strip()[:limite]
    slug = unidecode.unidecode(snippet)
    slug = re.sub(r"[^\w\s-]", "", slug).strip().lower()
    slug = re.sub(r"[\s]+", "_", slug)
    return slug

def criar_pasta_drive(slug: str, drive) -> str:
    meta = {"name": slug, "mimeType": "application/vnd.google-apps.folder", "parents": [GOOGLE_DRIVE_FOLDER_ID]}
    pasta = drive.files().create(body=meta, fields="id").execute()
    return pasta["id"]

def upload_arquivo_drive(filepath: Path, filename: str, folder_id: str, drive) -> str:
    file_meta = {"name": filename, "parents": [folder_id]}
    media     = MediaFileUpload(str(filepath), resumable=True)
    f         = drive.files().create(body=file_meta, media_body=media, fields="id").execute()
    return f["id"]

def format_ts(seconds: float) -> str:
    ms = int((seconds % 1) * 1000)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280,720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity, 128+intensity, (size[1],size[0],1), dtype=np.uint8)
        return np.repeat(noise, 3, axis=2)
    return VideoClip(frame, duration=1).set_fps(24)

def elevenlabs_tts(text: str, voice_id="cwIsrQsWEVTols6slKYN", retries=3) -> bytes:
    url     = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {"text": text, "voice_settings": {"stability":0.6, "similarity_boost":0.9, "style":0.2}}
    for attempt in range(retries):
        r = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
        if r.ok:
            return r.content
        time.sleep(2**attempt)
    raise RuntimeError("Não foi possível gerar TTS após várias tentativas")

@app.route("/")
def home():
    return "API OK"

@app.route("/audio/<path:fn>")
def servir_audio(fn):
    return send_from_directory(AUDIO_DIR, fn)

@app.route("/csv/<path:fn>")
def servir_csv(fn):
    return send_from_directory(CSV_DIR, fn)

@app.route("/downloads/<path:fn>")
def servir_down(fn):
    return send_from_directory(FILES_DIR, fn)

@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json() or {}
    texto = data.get("texto","").strip()
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug     = slugify(texto)
    filename = f"{slug}.mp3"
    path     = AUDIO_DIR / filename

    try:
        audio = elevenlabs_tts(texto)
        path.write_bytes(audio)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    return jsonify(audio_url=request.url_root.rstrip("/")+f"/audio/{filename}",
                   filename=filename,
                   slug=slug)

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url","").strip()
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    try:
        if audio_url.startswith(request.url_root.rstrip("/")):
            fname      = audio_url.rsplit("/audio/",1)[-1]
            audio_file = open(AUDIO_DIR/fname,"rb")
        else:
            r = requests.get(audio_url, timeout=60); r.raise_for_status()
            audio_file = io.BytesIO(r.content); audio_file.name="audio.mp3"

        srt_text = openai.audio.transcriptions.create(
            model="whisper-1", file=audio_file, response_format="srt"
        )

        def parse_ts(ts):
            h,m,rest = ts.split(":"); s,ms = rest.split(",")
            return int(h)*3600+int(m)*60+int(s)+int(ms)/1000

        segments=[]
        for blk in srt_text.strip().split("\n\n"):
            lines = blk.split("\n")
            if len(lines)>=3:
                start,end = lines[1].split(" --> ")
                txt        = " ".join(lines[2:])
                segments.append({
                    "inicio": parse_ts(start),
                    "fim":    parse_ts(end),
                    "texto":  txt
                })
        return jsonify(duracao_total=segments[-1]["fim"], transcricao=segments)

    except Exception as e:
        return jsonify(error=str(e)), 500

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data       = request.get_json() or {}
    transcricao= data.get("transcricao",[])
    prompts    = data.get("prompts",[])
    descricao  = data.get("descricao","").strip()
    slug       = data.get("slug") or str(uuid.uuid4())

    if not transcricao or not prompts or len(transcricao)!=len(prompts):
        return jsonify(error="transcricao+prompts inválidos"),400

    drive     = get_drive_service()
    pasta_id  = criar_pasta_drive(slug, drive)

    csv_path  = CSV_DIR / f"{slug}.csv"
    srt_path  = FILES_DIR / f"{slug}.srt"
    txt_path  = FILES_DIR / f"{slug}.txt"
    mp3s      = list(AUDIO_DIR.glob(f"{slug}.mp3"))

    # CSV
    header = ["PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL",
              "SEED_NUMBER","RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"]
    neg    = "low quality,overexposed,underexposed,extra limbs,disfigured"
    with open(csv_path,"w",newline="",encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(header)
        for seg,p in zip(transcricao,prompts):
            sec = int(round(seg["inicio"]))
            pf  = f"{sec} - {p}"
            w.writerow([pf,"PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT
    with open(srt_path,"w",encoding="utf-8") as s:
        for i,seg in enumerate(transcricao,1):
            s.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")

    # TXT
    with open(txt_path,"w",encoding="utf-8") as t:
        t.write(descricao)

    # Uploads
    upload_arquivo_drive(csv_path, f"{slug}.csv", pasta_id, drive)
    upload_arquivo_drive(srt_path, f"{slug}.srt", pasta_id, drive)
    upload_arquivo_drive(txt_path, f"{slug}.txt", pasta_id, drive)
    if mp3s:
        upload_arquivo_drive(mp3s[0], f"{slug}.mp3", pasta_id, drive)

    return jsonify(folder_url=f"https://drive.google.com/drive/folders/{pasta_id}")

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify(error="Campo 'zip' obrigatório"),400

    projetos = [p for p in FILES_DIR.iterdir() if p.is_dir() and not p.name.endswith("_raw")]
    if len(projetos)!=1:
        return jsonify(error="Deve haver exatamente uma pasta de projeto"),400

    slug       = projetos[0].name
    temp_dir   = FILES_DIR / f"{slug}_raw"
    output_dir = FILES_DIR / slug
    temp_dir.mkdir(exist_ok=True); output_dir.mkdir(exist_ok=True)

    zp = temp_dir/"imagens.zip"; file.save(zp)
    with zipfile.ZipFile(zp,"r") as z: z.extractall(temp_dir)

    imgs = list(temp_dir.glob("*.*"))
    return jsonify(ok=True, total=len(imgs), slug=slug)

@app.route("/montar_video", methods=["POST"])
def montar_video():
    data      = request.get_json(force=True)
    slug      = data.get("slug")
    folder_id = data.get("folder_id")
    pasta     = FILES_DIR/slug

    imgs = sorted([f for f in pasta.iterdir() if f.suffix.lower() in [".jpg",".png"]])
    if not imgs:
        return jsonify(error="Imagens não encontradas"),400

    # único MP3, CSV, SRT extraídos por slug
    audio = AUDIO_DIR/f"{slug}.mp3"
    srt   = FILES_DIR/f"{slug}.srt"
    prompts=[]
    with open(CSV_DIR/f"{slug}.csv", newline="", encoding="utf-8") as f:
        r=csv.reader(f); next(r)
        for row in r: prompts.append(row[0].split(" - ",1)[-1])

    # lê transcrição
    blocos=[] 
    for blk in open(srt,encoding="utf-8").read().split("\n\n"):
        lines=blk.split("\n")
        if len(lines)>=3:
            t0,t1 = lines[1].split(" --> ")
            def p(ts):
                h,m,rest=ts.split(":");s,ms=rest.split(",")
                return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
            blocos.append({"inicio":p(t0),"fim":p(t1),"texto":" ".join(lines[2:])})

    ac = AudioFileClip(str(audio))
    clips=[]
    for i,seg in enumerate(blocos):
        img = ImageClip(str(imgs[i%len(imgs)])).set_duration(seg["fim"]-seg["inicio"]).resize(height=720).crop(width=1280,x_center="center")
        txt=TextClip(seg["texto"].upper(),fontsize=60,font="DejaVu-Sans-Bold",color="white",stroke_color="black",stroke_width=2,method="caption",size=(1280,None)).set_duration(seg["fim"]-seg["inicio"]).set_position(("center","bottom"))
        g = make_grain().set_opacity(0.05).set_duration(seg["fim"]-seg["inicio"])
        comp = CompositeVideoClip([img,g,txt],size=(1280,720))
        clips.append(comp)

    final = concatenate_videoclips(clips).set_audio(ac)
    out = FILES_DIR/f"{slug}.mp4"
    final.write_videofile(str(out),fps=24,codec="libx264",audio_codec="aac")

    drive = get_drive_service()
    upload_arquivo_drive(out, f"{slug}.mp4", folder_id, drive)
    return jsonify(ok=True, video=f"https://drive.google.com/drive/folders/{folder_id}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)
