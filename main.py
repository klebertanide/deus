# main.py — versão final limpa

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

# ——— Configurações de caminhos ———
BASE        = Path(".")
AUDIO_DIR   = BASE / "audio";   AUDIO_DIR.mkdir(exist_ok=True)
CSV_DIR     = BASE / "csv";     CSV_DIR.mkdir(exist_ok=True)
FILES_DIR   = BASE / "downloads"; FILES_DIR.mkdir(exist_ok=True)

# ——— Variáveis de ambiente/API keys ———
GOOGLE_DRIVE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
ELEVEN_API_KEY         = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY             = os.getenv("OPENAI_API_KEY")
openai.api_key         = OPENAI_KEY

# ——— Utilitários de Drive ———
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_pasta_drive(slug, drive):
    meta = {
        "name": slug,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [GOOGLE_DRIVE_FOLDER_ID]
    }
    pasta = drive.files().create(body=meta, fields="id").execute()
    return pasta["id"]

def upload_arquivo_drive(path, filename, folder_id, drive):
    meta = {"name": filename, "parents": [folder_id]}
    media = MediaFileUpload(str(path), resumable=True)
    f = drive.files().create(body=meta, media_body=media, fields="id").execute()
    return f["id"]

# ——— Funções auxiliares ———
def slugify(texto, limite=30):
    txt = unidecode.unidecode(texto)
    txt = re.sub(r"[^\w\s]", "", txt).strip().replace(" ", "_")
    return txt[:limite].lower()

def format_ts(segundos):
    ms = int((segundos % 1) * 1000)
    h  = int(segundos // 3600)
    m  = int((segundos % 3600) // 60)
    s  = int(segundos % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280,720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity,128+intensity,(size[1],size[0],1), dtype=np.uint8)
        noise = np.repeat(noise,3,axis=2)
        return noise
    return VideoClip(frame, duration=1).set_fps(24)

# ——— TTS ElevenLabs ———
def elevenlabs_tts(texto, voice="cwIsrQsWEVTols6slKYN", retries=3):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}/stream"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}

    def request_tts(payload):
        for i in range(retries):
            r = requests.post(url, json=payload, headers=headers, stream=True, timeout=60)
            if r.ok:
                return r.content
            time.sleep(2**i)
        r.raise_for_status()

    # primeiro com style, senão sem style
    payload = {"text": texto,
               "voice_settings": {"stability":0.6, "similarity_boost":0.9, "style":0.2}}
    try:
        return request_tts(payload)
    except:
        payload["voice_settings"].pop("style",None)
        return request_tts(payload)

# ——— Rotas estáticas ———
@app.route("/audio/<path:fn>")
def serve_audio(fn):   return send_from_directory(AUDIO_DIR, fn)
@app.route("/csv/<path:fn>")
def serve_csv(fn):     return send_from_directory(CSV_DIR, fn)
@app.route("/downloads/<path:fn>")
def serve_files(fn):   return send_from_directory(FILES_DIR, fn)

# ——— 1) /falar — gera TTS ———
@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json(force=True)
    texto = data.get("texto")
    if not texto:
        return jsonify({"error":"campo 'texto' obrigatório"}),400

    slug     = slugify(texto)
    mp3_file = AUDIO_DIR / f"{slug}.mp3"
    audio    = elevenlabs_tts(texto)
    mp3_file.write_bytes(audio)

    return jsonify({
        "audio_url": f"{request.url_root.rstrip('/')}/audio/{slug}.mp3",
        "slug": slug
    })

# ——— 2) /transcrever — usa Whisper ———
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json(force=True)
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error":"campo 'audio_url' obrigatório"}),400

    # obtém bytes
    if audio_url.startswith(request.url_root.rstrip("/")):
        fname = audio_url.rsplit("/audio/",1)[-1]
        fobj  = open(AUDIO_DIR/fname,"rb")
    else:
        r = requests.get(audio_url, timeout=60)
        r.raise_for_status()
        fobj = io.BytesIO(r.content); fobj.name="audio.mp3"

    # chamada correta para Whisper
    resp = openai.Audio.transcribe("whisper-1", file=fobj, response_format="verbose_json")
    fobj.close()

    # monta segmentos
    segs = []
    for s in resp["segments"]:
        segs.append({
            "inicio": s["start"], "fim": s["end"], "texto": s["text"].strip()
        })
    return jsonify({"duracao_total":resp["duration"], "transcricao":segs})

# ——— 3) /gerar_csv — cria CSV, SRT, TXT no Drive ———
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data        = request.get_json(force=True)
    transcricao = data.get("transcricao", [])
    prompts     = data.get("prompts", [])
    descricao   = data.get("descricao","")
    slug        = data.get("slug")
    if not slug:
        return jsonify({"error":"campo 'slug' obrigatório"}),400

    # valida
    if not transcricao or not prompts or len(transcricao)!=len(prompts):
        return jsonify({"error":"transcricao+prompts inválidos"}),400

    # paths
    csv_path = CSV_DIR / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"

    # CSV
    neg = ("low quality, overexposed, underexposed, extra limbs, extra fingers, "
           "missing fingers, disfigured, deformed, bad anatomy")
    header = ["PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL",
              "SEED_NUMBER","RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"]
    with open(csv_path,"w",newline="",encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for seg,p in zip(transcricao,prompts):
            t0 = int(round(seg["inicio"]))
            prompt_full = f"{t0} - {p}"
            writer.writerow([prompt_full,"PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT
    with open(srt_path,"w",encoding="utf-8") as f:
        for i,s in enumerate(transcricao,1):
            f.write(f"{i}\n{format_ts(s['inicio'])} --> {format_ts(s['fim'])}\n{s['texto']}\n\n")

    # TXT
    with open(txt_path,"w",encoding="utf-8") as f:
        f.write(descricao.strip())

    # upload
    drive    = get_drive_service()
    pasta_id = criar_pasta_drive(slug, drive)
    upload_arquivo_drive(csv_path, f"{slug}.csv", pasta_id, drive)
    upload_arquivo_drive(srt_path, f"{slug}.srt", pasta_id, drive)
    upload_arquivo_drive(txt_path, f"{slug}.txt", pasta_id, drive)

    return jsonify({"folder_url":f"https://drive.google.com/drive/folders/{pasta_id}"})


# ——— 4) /upload_zip — recebe ZIP e extrai imagens ———
@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify({"error":"campo 'zip' obrigatório"}),400

    # slug da pasta existente
    pastas = [d for d in FILES_DIR.iterdir() if d.is_dir() and not d.name.endswith("_raw")]
    if len(pastas)!=1:
        return jsonify({"error":"pasta de projeto não única"}),400
    slug       = pastas[0].name
    temp_dir   = FILES_DIR / f"{slug}_raw";   temp_dir.mkdir(exist_ok=True)
    output_dir = FILES_DIR / slug;            output_dir.mkdir(exist_ok=True)

    zip_path = temp_dir / "imagens.zip"; file.save(zip_path)
    with zipfile.ZipFile(zip_path,"r") as z: z.extractall(temp_dir)

    imgs = [f for f in temp_dir.iterdir() if f.suffix.lower() in [".jpg",".png",".jpeg"]]
    if not imgs:
        return jsonify({"error":"nenhuma imagem no zip"}),400

    # lê prompts do CSV
    csv_p = CSV_DIR / f"{slug}.csv"
    if not csv_p.exists():
        return jsonify({"error":"CSV não encontrado"}),400

    prompts=[]
    with open(csv_p,newline="",encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            prompts.append(row["PROMPT"].split(" - ",1)[-1])

    # match por similaridade simples
    for i,p in enumerate(prompts):
        best = max(imgs, key=lambda f: re.sub(r"[^\w\s]"," ",f.stem).lower().count(p.lower().split()[0]))
        dest = output_dir / f"{i:02d}_{best.name}"
        best.rename(dest)
        imgs.remove(best)

    return jsonify({"ok":True, "slug":slug, "total":len(prompts)})

# ——— 5) /montar_video — compõe vídeo final ———
@app.route("/montar_video", methods=["POST"])
def montar_video():
    data      = request.get_json(force=True)
    slug      = data.get("slug");      folder_id = data.get("folder_id")
    pasta_loc = FILES_DIR / slug
    images    = sorted(pasta_loc.glob("*.*"))
    mp3s      = list(AUDIO_DIR.glob(f"{slug}.mp3"))
    srt_files = list(FILES_DIR.glob(f"{slug}.srt"))

    if not mp3s or not srt_files:
        return jsonify({"error":"MP3 ou SRT não encontrado"}),400

    # carrega transcrição
    trans=[]
    with open(srt_files[0],encoding="utf-8") as f:
        blocks = f.read().strip().split("\n\n")
        for b in blocks:
            l = b.split("\n")
            start,end = l[1].split(" --> ")
            text = " ".join(l[2:])
            trans.append({
                "inicio": sum(float(x)*60**i for i,x in enumerate(reversed(start.replace(",",".").split(":")))),
                "fim":    sum(float(x)*60**i for i,x in enumerate(reversed(end.replace(",",".").split(":")))),
                "texto":  text
            })

    audio = AudioFileClip(str(mp3s[0]))
    clips=[]
    for idx,seg in enumerate(trans):
        dur = seg["fim"]-seg["inicio"]
        pic = ImageClip(str(images[idx % len(images)]))\
                .resize(height=720).crop(x_center="center",width=1280)\
                .set_duration(dur)
        txt = TextClip(seg["texto"].upper(), fontsize=60, font="DejaVu-Sans-Bold",
                       stroke_color="black",stroke_width=2, size=(1280,None),
                       method="caption")\
                .set_duration(dur).set_position(("center","bottom"))
        grain = make_grain().set_opacity(0.05).set_duration(dur)
        comp = CompositeVideoClip([pic, grain, txt], size=(1280,720))
        clips.append(comp)

    final = concatenate_videoclips(clips).set_audio(audio)
    out   = FILES_DIR / f"{slug}.mp4"
    final.write_videofile(str(out),fps=24,codec="libx264",audio_codec="aac")

    # envia Drive
    drive = get_drive_service()
    upload_arquivo_drive(out, f"{slug}.mp4", folder_id, drive)
    return jsonify({"video_url":f"https://drive.google.com/drive/folders/{folder_id}"})

# ——— Plugin / OpenAPI — serve docs ———
@app.route("/.well-known/openapi.json")
def serve_oapi():
    return send_from_directory(".well-known","openapi.json",mimetype="application/json")

@app.route("/.well-known/ai-plugin.json")
def serve_plugin():
    return send_from_directory(".well-known","ai-plugin.json",mimetype="application/json")

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)