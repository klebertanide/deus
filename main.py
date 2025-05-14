# main.py

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

# ————— CONFIGURAÇÕES —————
app = Flask(__name__)

# Seu domínio público
BASE_URL                    = "https://deus-w0i8.onrender.com"

# Diretórios locais (tudo no raiz do projeto)
BASE_DIR                    = Path(".")
AUDIO_DIR                   = BASE_DIR / "audio"
CSV_DIR                     = BASE_DIR / "csv"
FILES_DIR                   = BASE_DIR / "downloads"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# IDs e credenciais
GOOGLE_DRIVE_FOLDER_ID      = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
SERVICE_ACCOUNT_JSON_PATH   = "/etc/secrets/service_account.json"
ELEVENLABS_API_KEY          = os.getenv("ELEVENLABS_API_KEY")
OPENAI_API_KEY              = os.getenv("OPENAI_API_KEY")
openai.api_key              = OPENAI_API_KEY

# ————— UTILITÁRIOS —————

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_JSON_PATH,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(texto: str, limite: int = 30) -> str:
    texto = unidecode.unidecode(texto)
    texto = re.sub(r"[^\w\s]", "", texto).strip().replace(" ", "_")
    return texto[:limite].lower()

def criar_pasta_drive(slug: str, drive):
    meta = {
        "name": slug,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [GOOGLE_DRIVE_FOLDER_ID]
    }
    pasta = drive.files().create(body=meta, fields="id").execute()
    return pasta["id"]

def upload_arquivo_drive(filepath: Path, filename: str, folder_id: str, drive):
    meta  = {"name": filename, "parents": [folder_id]}
    media = MediaFileUpload(str(filepath), resumable=True)
    f     = drive.files().create(body=meta, media_body=media, fields="id").execute()
    return f["id"]

def format_ts(seconds: float) -> str:
    ms = int((seconds % 1) * 1000)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280, 720), intensity=10):
    def frame(t):
        noise = np.random.randint(
            128-intensity, 128+intensity,
            (size[1], size[0], 1), dtype=np.uint8
        )
        return np.repeat(noise, 3, axis=2)
    return VideoClip(frame, duration=1).set_fps(24)

def selecionar_imagem_mais_similar(prompt, imagens):
    from sentence_transformers import SentenceTransformer, util
    model      = SentenceTransformer("clip-ViT-B-32")
    prom_emb   = model.encode(prompt, convert_to_tensor=True)
    best_score = -1
    best_img   = None
    for img in imagens:
        clean     = re.sub(r"[^\w\s]", " ", img.stem)
        name_emb  = model.encode(clean, convert_to_tensor=True)
        score     = util.cos_sim(prom_emb, name_emb).item()
        if score > best_score:
            best_score, best_img = score, img
    return best_img

def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN", retries=3):
    def post(payload):
        url     = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
        for i in range(retries):
            r = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
            if r.ok:
                return r.content
            time.sleep(2**i)
        raise RuntimeError("ElevenLabs TTS falhou")
    try:
        return post({"text": text, "voice_settings":{"stability":0.6,"similarity_boost":0.9,"style":0.2}})
    except:
        return post({"text": text, "voice_settings":{"stability":0.6,"similarity_boost":0.9}})

# ————— ROTAS BÁSICAS —————

@app.route("/", methods=["GET"])
def home():
    return "API DeusTeEnviouIsso no ar!"

@app.route("/audio/<path:fn>", methods=["GET"])
def servir_audio(fn):
    return send_from_directory(AUDIO_DIR, fn)

@app.route("/csv/<path:fn>", methods=["GET"])
def servir_csv(fn):
    return send_from_directory(CSV_DIR, fn)

@app.route("/downloads/<path:fn>", methods=["GET"])
def servir_downloads(fn):
    return send_from_directory(FILES_DIR, fn)

# ————— /falar —————

@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify({"error":"campo 'texto' obrigatório"}), 400

    slug     = slugify(texto)
    filename = f"{slug}.mp3"
    out_path = AUDIO_DIR / filename

    try:
        audio = elevenlabs_tts(texto)
        out_path.write_bytes(audio)
    except Exception as e:
        return jsonify({"error":"ElevenLabs falhou","detail":str(e)}), 500

    return jsonify({
        "audio_url": f"{BASE_URL}/audio/{filename}",
        "filename": filename,
        "slug": slug
    })

# ————— /transcrever —————

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url")

    # auto-detectar se não enviado
    if not audio_url:
        mp3s = list(AUDIO_DIR.glob("*.mp3"))
        if len(mp3s)==1:
            audio_file = open(mp3s[0],"rb")
        else:
            return jsonify({"error":"informe audio_url ou tenha exatamente 1 mp3 na pasta"}),400
    else:
        if audio_url.startswith(BASE_URL):
            fname      = audio_url.rsplit("/audio/",1)[-1]
            audio_file = open(AUDIO_DIR/fname,"rb")
        else:
            r = requests.get(audio_url,timeout=60); r.raise_for_status()
            audio_file = io.BytesIO(r.content); audio_file.name="audio.mp3"

    try:
        srt_text = openai.Audio.transcribe(
            model="whisper-1", file=audio_file, response_format="srt"
        )
        def parse_ts(ts):
            h,m,rest = ts.split(":")
            s,ms = rest.split(",")
            return int(h)*3600+int(m)*60+int(s)+int(ms)/1000

        segments=[]
        for blk in srt_text.strip().split("\n\n"):
            lines=blk.split("\n")
            if len(lines)>=3:
                st,et=lines[1].split(" --> ")
                txt=" ".join(lines[2:])
                segments.append({
                    "inicio": parse_ts(st),
                    "fim":    parse_ts(et),
                    "texto":  txt
                })
        total = segments[-1]["fim"] if segments else 0.0
        return jsonify({"duracao_total": total, "transcricao": segments})
    except Exception as e:
        return jsonify({"error":str(e)}),500
    finally:
        try: audio_file.close()
        except: pass

# ————— /gerar_csv —————

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data      = request.get_json() or {}
    transcr   = data.get("transcricao",[])
    prompts   = data.get("prompts",[])
    descricao = data.get("descricao","")
    slug      = data.get("slug") or slugify(descricao or uuid.uuid4().hex)

    if not transcr:
        return jsonify({"error":"transcricao inválida"}),400

    if not prompts:
        # auto carregamento de prompts existentes em CSV anterior
        csv_old = CSV_DIR/f"{slug}.csv"
        if csv_old.exists():
            with open(csv_old,newline="",encoding="utf-8") as f:
                prompts=[r["PROMPT"].split(" - ",1)[-1] for r in csv.DictReader(f)]
        else:
            return jsonify({"error":"prompts faltando"}),400

    base = slugify(descricao or slug)

    mp3s = list(AUDIO_DIR.glob("*.mp3"))
    if not mp3s:
        return jsonify({"error":"nenhum mp3"}),400
    mp3_path = mp3s[0]

    csv_path = CSV_DIR / f"{base}.csv"
    srt_path = FILES_DIR / f"{base}.srt"
    txt_path = FILES_DIR / f"{base}.txt"

    # CSV
    header=["PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT",
            "MODEL","SEED_NUMBER","RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"]
    neg="low quality,bad anatomy"
    with open(csv_path,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(header)
        for seg,p in zip(transcr,prompts):
            t0=int(seg["inicio"])
            pr=f"{t0} - {p}"
            w.writerow([pr,"PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT
    with open(srt_path,"w",encoding="utf-8") as f:
        for i,seg in enumerate(transcr,1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")

    # TXT
    with open(txt_path,"w",encoding="utf-8") as f:
        f.write(descricao.strip())

    drive    = get_drive_service()
    pasta_id = criar_pasta_drive(base, drive)
    upload_arquivo_drive(csv_path, f"{base}.csv", pasta_id, drive)
    upload_arquivo_drive(srt_path, f"{base}.srt", pasta_id, drive)
    upload_arquivo_drive(txt_path, f"{base}.txt", pasta_id, drive)
    upload_arquivo_drive(mp3_path,  "voz.mp3", pasta_id, drive)

    return jsonify({"folder_url":f"https://drive.google.com/drive/folders/{pasta_id}"})

# ————— /upload_zip —————

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify({"error":"campo 'zip' obrigatório"}),400

    # slug da última pasta criada
    pastas = sorted((FILES_DIR).iterdir(), key=lambda p:p.stat().st_mtime, reverse=True)
    if not pastas or not pastas[0].is_dir():
        return jsonify({"error":"nenhuma pasta"}),400
    slug = pastas[0].name

    temp = FILES_DIR / f"{slug}_raw"
    outp = FILES_DIR / slug
    temp.mkdir(exist_ok=True); outp.mkdir(exist_ok=True)

    zip_path = temp/"imgs.zip"
    file.save(zip_path)
    with zipfile.ZipFile(zip_path) as z: z.extractall(temp)

    imgs=[f for f in temp.iterdir() if f.suffix.lower() in (".jpg",".png",".jpeg")]
    if not imgs:
        return jsonify({"error":"nenhuma imagem"}),400

    csvp = CSV_DIR/f"{slug}.csv"
    if not csvp.exists():
        return jsonify({"error":"csv não encontrado"}),400

    prompts=[]
    with open(csvp,newline="",encoding="utf-8") as f:
        for r in csv.DictReader(f):
            prompts.append(r["PROMPT"].split(" - ",1)[-1])

    selecionadas=[]
    for i,p in enumerate(prompts):
        best = selecionar_imagem_mais_similar(p, imgs)
        if best:
            dest=outp/f"{i:02d}_{best.name}"
            best.rename(dest)
            selecionadas.append(dest.name)
            imgs.remove(best)

    return jsonify({"ok":True,"slug":slug,"usadas":selecionadas})

# ————— /montar_video —————

@app.route("/montar_video", methods=["POST"])
def montar_video():
    data      = request.get_json(force=True)
    slug      = data.get("slug")
    folder_id = data.get("folder_id")
    if not slug or not folder_id:
        return jsonify({"error":"slug e folder_id obrigatórios"}),400

    vid_dir    = FILES_DIR/slug
    imagens    = sorted([f for f in vid_dir.iterdir() if f.is_file()])
    mp3_path   = next(AUDIO_DIR.glob("*.mp3"), None)
    srt_path   = next(FILES_DIR.glob("*.srt"), None)
    if not imagens or not mp3_path or not srt_path:
        return jsonify({"error":"arquivos faltando"}),400

    # lê legendas
    with open(srt_path,encoding="utf-8") as f:
        blocks=f.read().split("\n\n")
    transcr=[]
    for blk in blocks:
        lines=blk.split("\n")
        if len(lines)>=3:
            st,et=lines[1].split(" --> ")
            txt=" ".join(lines[2:])
            def to_sec(ts):
                h,m,rest=ts.split(":"); s,ms=rest.split(",")
                return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
            transcr.append({"inicio":to_sec(st),"fim":to_sec(et),"texto":txt})

    audio_clip=AudioFileClip(str(mp3_path))
    clips=[]
    for i,seg in enumerate(transcr):
        dur=seg["fim"]-seg["inicio"]
        img=ImageClip(str(imagens[i])).resize(height=720)\
             .crop(x_center="center",width=1280).set_duration(dur)
        zoom=img.resize(lambda t:1+0.02*t)
        legend=TextClip(seg["texto"].upper(),fontsize=60,font="DejaVu-Sans-Bold",
                        color="white",stroke_color="black",stroke_width=2,
                        method="caption",size=(1280,None))\
               .set_duration(dur).set_position(("center","bottom"))
        grain=make_grain().set_opacity(0.05).set_duration(dur)
        comp=CompositeVideoClip([zoom,grain,legend],size=(1280,720))
        clips.append(comp)

    final=concatenate_videoclips(clips).set_audio(audio_clip)
    outp=FILES_DIR/f"{slug}.mp4"
    final.write_videofile(str(outp),fps=24,codec="libx264",audio_codec="aac")

    drive=get_drive_service()
    upload_arquivo_drive(outp,"video_final.mp4",folder_id,drive)
    return jsonify({"ok":True,"folder":f"https://drive.google.com/drive/folders/{folder_id}"})

# ————— RODAR —————
if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)