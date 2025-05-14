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
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# ——————————————— Configurações de pastas ———————————————
BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR = BASE / "csv"
FILES_DIR = BASE / "downloads"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY     = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_KEY

# ——————————————— Helpers ———————————————
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

def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280, 720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity, 128+intensity, (size[1], size[0], 1), dtype=np.uint8)
        noise = np.repeat(noise, 3, axis=2)
        return noise
    return VideoClip(frame, duration=1).set_fps(24)

# ——————————————— Rotas públicas ———————————————
@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

@app.route("/audio/<path:fn>")
def serve_audio(fn):
    return send_from_directory(AUDIO_DIR, fn)

@app.route("/csv/<path:fn>")
def serve_csv(fn):
    return send_from_directory(CSV_DIR, fn)

@app.route("/downloads/<path:fn>")
def serve_download(fn):
    return send_from_directory(FILES_DIR, fn)

# ——————————————— /falar ———————————————
@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug     = slugify(texto)
    filename = f"{slug}.mp3"
    outpath  = AUDIO_DIR / filename

    # Chama sua função ElevenLabs TTS aqui...
    try:
        # Exemplo genérico; troque pela sua implementação elevenlabs_tts()
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

# ——————————————— /transcrever ———————————————
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    # carrega o único mp3 se for relativo, ou faz GET se for externo
    if audio_url.startswith(request.url_root.rstrip("/")):
        fname = audio_url.rsplit("/audio/", 1)[-1]
        file  = open(AUDIO_DIR / fname, "rb")
    else:
        resp = requests.get(audio_url, timeout=60)
        resp.raise_for_status()
        file = io.BytesIO(resp.content); file.name = "audio.mp3"

    try:
        # gerar SRT via Whisper
        srt = openai.audio.transcriptions.create(
            model="whisper-1",
            file=file,
            response_format="srt"
        )
        # parsear SRT em segmentos
        def parse_ts(ts):
            h,m,rest = ts.split(":")
            s,ms     = rest.split(",")
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

        segmentos = []
        for bloco in srt.strip().split("\n\n"):
            lines = bloco.split("\n")
            if len(lines) < 3: continue
            start, end = lines[1].split(" --> ")
            texto      = " ".join(lines[2:])
            segmentos.append({
                "inicio": parse_ts(start),
                "fim":    parse_ts(end),
                "texto":  texto
            })

        return jsonify(duracao_total=segmentos[-1]["fim"], transcricao=segmentos)

    except Exception as e:
        return jsonify(error=str(e)), 500

    finally:
        try: file.close()
        except: pass

# ——————————————— /gerar_csv ———————————————
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data       = request.get_json() or {}
    transcricao = data.get("transcricao", [])
    prompts     = data.get("prompts", [])
    descricao   = data.get("descricao", "")
    slug        = data.get("slug") or str(uuid.uuid4())

    # validações
    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    # cria pasta no Drive
    drive    = get_drive_service()
    folderId = criar_pasta_drive(slug, drive)

    # caminhos locais
    csv_path = CSV_DIR / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"

    # escreve CSV
    header = ["TIME","PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL","SEED","RENDERING","NEGATIVE","STYLE","PALETTE"]
    neg    = "low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for seg,p in zip(transcricao, prompts):
            t = int(seg["inicio"])
            prompt_final = f"{t} - {p}"
            w.writerow([t, prompt_final, "PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT
    with open(srt_path, "w", encoding="utf-8") as f:
        for i,seg in enumerate(transcricao,1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")

    # TXT
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(descricao.strip())

    # upload Drive
    upload_arquivo_drive(csv_path, "prompts.csv", folderId, drive)
    upload_arquivo_drive(srt_path, "legenda.srt",    folderId, drive)
    upload_arquivo_drive(txt_path, "descricao.txt",  folderId, drive)

    return jsonify(folder_url=f"https://drive.google.com/drive/folders/{folderId}")

# ——————————————— /upload_zip ———————————————
@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify(error="Campo 'zip' obrigatório."), 400

    # detecta único projeto existente
    projs = [p for p in FILES_DIR.iterdir() if p.is_dir()]
    if len(projs)!=1:
        return jsonify(error="Espere uma única pasta de projeto."), 400
    slug = projs[0].name
    tmp  = FILES_DIR / f"{slug}_raw"
    out  = FILES_DIR / slug
    tmp.mkdir(exist_ok=True); out.mkdir(exist_ok=True)

    zip_path = tmp/"imgs.zip"
    file.save(zip_path)
    with zipfile.ZipFile(zip_path) as z: z.extractall(tmp)

    imgs = [f for f in tmp.iterdir() if f.suffix.lower() in (".jpg",".jpeg",".png")]
    if not imgs:
        return jsonify(error="Nenhuma imagem no ZIP."), 400

    # lê prompts do CSV
    csv_path = CSV_DIR / f"{slug}.csv"
    if not csv_path.exists():
        return jsonify(error="CSV não encontrado."), 400
    prompts=[]
    with open(csv_path, encoding="utf-8") as f:
        reader=csv.DictReader(f)
        for r in reader:
            prompts.append(r["PROMPT"].split(" - ",1)[-1])

    # aqui você implementa sua lógica de similaridade, por exemplo com CLIP
    selecionadas = []
    for idx,p in enumerate(prompts):
        # placeholder: pega a primeira
        img = imgs[0]
        dst = out / f"{idx:02d}_{img.name}"
        img.rename(dst)
        selecionadas.append(dst.name)

    return jsonify(ok=True, slug=slug, usadas=selecionadas)

# ——————————————— /montar_video ———————————————
@app.route("/montar_video", methods=["POST"])
def montar_video():
    data     = request.get_json(force=True) or {}
    slug     = data.get("slug")
    folderId = data.get("folder_id")

    # pega imagens
    loc = FILES_DIR/slug
    imgs = sorted([f for f in loc.iterdir() if f.suffix.lower() in (".jpg",".png",".jpeg")])
    if not imgs:
        return jsonify(error="Sem imagens."), 400

    # único mp3
    mp3s = list(AUDIO_DIR.glob("*.mp3"))
    if not mp3s:
        return jsonify(error="Áudio não encontrado."), 400
    audio_clip = AudioFileClip(str(mp3s[0]))

    # transcrição SRT
    srt = list(FILES_DIR.glob("*.srt"))
    if not srt:
        return jsonify(error="Legenda não encontrada."), 400
    # parse SRT como antes...
    segmentos=[]
    with open(srt[0], encoding="utf-8") as f:
        for bloco in f.read().split("\n\n"):
            lines=bloco.split("\n")
            if len(lines)>=3:
                i1,i2 = lines[1].split(" --> ")
                # parse timestamps
                segmentos.append({"inicio":0,"fim":3,"texto":lines[2]})

    clips=[]
    for idx,seg in enumerate(segmentos):
        dur = seg["fim"]-seg["inicio"]
        img_clip = ImageClip(str(imgs[idx%len(imgs)])).resize(height=720).crop(x_center="center",width=1280).set_duration(dur)
        zoom     = img_clip.resize(lambda t:1+0.02*t)
        txtclip  = TextClip(seg["texto"], fontsize=60, color="white", stroke_color="black", stroke_width=2, method="caption").set_duration(dur).set_position(("center","bottom"))
        grain    = make_grain().set_opacity(0.05).set_duration(dur)
        comp     = CompositeVideoClip([zoom, grain, txtclip], size=(1280,720))
        clips.append(comp)

    final = concatenate_videoclips(clips).set_audio(audio_clip)
    outp  = FILES_DIR/f"{slug}.mp4"
    final.write_videofile(str(outp), fps=24, codec="libx264", audio_codec="aac")

    drive=get_drive_service()
    upload_arquivo_drive(outp, "video.mp4", folderId, drive)
    return jsonify(video_url=f"https://drive.google.com/drive/folders/{folderId}")

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)