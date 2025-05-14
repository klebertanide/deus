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

# ——— Configurações de pastas locais ———
BASE        = Path(".")
AUDIO_DIR   = BASE / "audio"
CSV_DIR     = BASE / "csv"
FILES_DIR   = BASE / "downloads"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# ——— Credenciais e IDs ———
GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY         = os.getenv("ELEVENLABS_API_KEY")
OPENAI_API_KEY         = os.getenv("OPENAI_API_KEY")
openai.api_key         = OPENAI_API_KEY

# ——— Utilitários ———
def slugify(texto: str, limite: int = 30) -> str:
    txt = unidecode.unidecode(texto)
    txt = re.sub(r"(?i)^deus\s+", "", txt)
    txt = re.sub(r"[^\w\s]", "", txt)
    txt = txt.strip().replace(" ", "_")
    return txt[:limite].lower()

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_pasta_drive(slug: str, drive) -> str:
    body = {
        "name": slug,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [GOOGLE_DRIVE_FOLDER_ID]
    }
    pasta = drive.files().create(body=body, fields="id").execute()
    return pasta["id"]

def upload_arquivo_drive(path: Path, nome: str, folder_id: str, drive) -> str:
    meta = {"name": nome, "parents": [folder_id]}
    media = MediaFileUpload(str(path), resumable=True)
    arq = drive.files().create(body=meta, media_body=media, fields="id").execute()
    return arq["id"]

def format_ts(segundos: float) -> str:
    ms = int((segundos % 1) * 1000)
    h  = int(segundos // 3600)
    m  = int((segundos % 3600) // 60)
    s  = int(segundos % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280,720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity,128+intensity,(size[1],size[0],1),np.uint8)
        noise = np.repeat(noise,3,axis=2)
        return noise
    return VideoClip(frame, duration=1).set_fps(24)


# ——— Rotas estáticas ———
@app.route("/")
def home(): return "API DeusTeEnviouIsso OK"
@app.route("/audio/<path:f>")     def audio(f):   return send_from_directory(AUDIO_DIR, f)
@app.route("/csv/<path:f>")       def csv_dl(f): return send_from_directory(CSV_DIR, f)
@app.route("/downloads/<path:f>") def dl(f):     return send_from_directory(FILES_DIR, f)
@app.route("/.well-known/openapi.json")
def openapi(): return send_from_directory(".well-known", "openapi.json", mimetype="application/json")
@app.route("/.well-known/ai-plugin.json")
def plugin(): return send_from_directory(".well-known", "ai-plugin.json", mimetype="application/json")


# ——— 1) Gera áudio com ElevenLabs ———
@app.route("/falar", methods=["POST"])
def falar():
    txt = (request.get_json() or {}).get("texto")
    if not txt: return jsonify({"error":"campo 'texto' obrigatório"}),400

    slug     = slugify(txt)
    mp3_path = AUDIO_DIR / f"{slug}.mp3"

    # faz TTS
    url     = f"https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN/stream"
    headers = {"xi-api-key":ELEVEN_API_KEY,"Content-Type":"application/json"}
    payload = {"text":txt,"voice_settings":{"stability":0.6,"similarity_boost":0.9,"style":0.2}}
    try:
        resp = requests.post(url,headers=headers,json=payload,stream=True,timeout=60)
        resp.raise_for_status()
        data = resp.content or b""
        mp3_path.write_bytes(data)
    except Exception as e:
        return jsonify({"error":"ElevenLabs falhou","detalhe":str(e)}),500

    return jsonify({
        "slug":slug,
        "audio_url":request.url_root.rstrip("/")+f"/audio/{slug}.mp3"
    })


# ——— 2) Transcreve via Whisper (SRT) ———
@app.route("/transcrever", methods=["POST"])
def transcrever():
    slug = (request.get_json() or {}).get("slug")
    if not slug: return jsonify({"error":"campo 'slug' obrigatório"}),400

    mp3 = AUDIO_DIR / f"{slug}.mp3"
    if not mp3.exists(): return jsonify({"error":"MP3 não encontrado"}),400

    # gera SRT
    with open(mp3,"rb") as f:
        srt = openai.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="srt"
        )

    # parse do SRT em segmentos
    def p_ts(ts):
        h,m,rest = ts.split(":")
        s,ms     = rest.split(",")
        return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

    segs=[]
    for bloco in srt.strip().split("\n\n"):
        l = bloco.split("\n")
        if len(l)>=3:
            a,b = l[1].split(" --> ")
            text = " ".join(l[2:])
            segs.append({"inicio":p_ts(a),"fim":p_ts(b),"texto":text})

    dur = segs[-1]["fim"] if segs else 0
    return jsonify({"duracao_total":dur,"transcricao":segs})


# ——— 3) Gera CSV, SRT, TXT e faz upload ao Drive ———
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    d      = request.get_json() or {}
    segs   = d.get("transcricao",[])
    prompts= d.get("prompts",[])
    desc   = d.get("descricao","")
    slug   = d.get("slug")
    if not slug: return jsonify({"error":"campo 'slug' obrigatório"}),400
    if not segs or not prompts or len(segs)!=len(prompts):
        return jsonify({"error":"transcricao+prompts inválidos"}),400

    mp3 = AUDIO_DIR/ f"{slug}.mp3"
    if not mp3.exists(): return jsonify({"error":"MP3 não encontrado"}),400

    drive   = get_drive_service()
    folder  = criar_pasta_drive(slug, drive)

    # CSV
    csv_path= CSV_DIR / f"{slug}.csv"
    hdr     = ["PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL",
               "SEED_NUMBER","RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"]
    neg     = "low quality, overexposed, underexposed, extra limbs, disfigured"
    with open(csv_path,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(hdr)
        for s,p in zip(segs,prompts):
            sec = int(round(s["inicio"]))
            prompt = f"{sec} - Vibrant watercolor illustration of {p}"
            w.writerow([prompt,"PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT e TXT
    srt_path= FILES_DIR/ f"{slug}.srt"
    txt_path= FILES_DIR/ f"{slug}.txt"
    with open(srt_path,"w",encoding="utf-8") as f:
        for i,s in enumerate(segs,1):
            f.write(f"{i}\n{format_ts(s['inicio'])} --> {format_ts(s['fim'])}\n{s['texto']}\n\n")
    with open(txt_path,"w",encoding="utf-8") as f:
        f.write(desc.strip())

    # Upload
    upload_arquivo_drive(csv_path, f"{slug}.csv", folder, drive)
    upload_arquivo_drive(srt_path, f"{slug}.srt", folder, drive)
    upload_arquivo_drive(txt_path, f"{slug}.txt", folder, drive)
    upload_arquivo_drive(mp3,      f"{slug}.mp3", folder, drive)

    return jsonify({"folder_url":f"https://drive.google.com/drive/folders/{folder}"})


# ——— 4) Recebe ZIP de imagens, associa ao CSV e seleciona a melhor ———
@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    zom = request.files.get("zip")
    if not zom: return jsonify({"error":"campo 'zip' obrigatório"}),400

    # descobre slug existente (único)
    dirs = [p for p in FILES_DIR.iterdir() if p.is_dir()]
    if not dirs: return jsonify({"error":"nenhuma pasta de projeto"}),400
    slug = dirs[0].name
    base = FILES_DIR/slug
    raw  = FILES_DIR/f"{slug}_raw"
    raw.mkdir(exist_ok=True); base.mkdir(exist_ok=True)

    # salva+extrai ZIP
    zf = raw/"imgs.zip"; zom.save(zf)
    with zipfile.ZipFile(zf,"r") as z: z.extractall(raw)

    imgs = list(raw.glob("*.*"))
    if not imgs: return jsonify({"error":"nenhuma imagem no ZIP"}),400

    # lê prompts do CSV
    csvf = CSV_DIR/f"{slug}.csv"
    if not csvf.exists(): return jsonify({"error":"CSV não encontrado"}),400
    ps=[]
    with open(csvf, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ps.append(r["PROMPT"].split(" - ",1)[-1])

    # seleciona a melhor por simples similaridade de nome
    sel=[]
    for i,p in enumerate(ps):
        best = max(imgs, key=lambda img: SequenceMatcher(None,p,img.stem).ratio())
        dst  = base/f"{i:02d}_{best.name}"
        best.rename(dst); sel.append(dst.name)
        imgs.remove(best)

    return jsonify({"ok":True,"slug":slug,"selecionadas":sel})


# ——— 5) Monta o vídeo final ———
@app.route("/montar_video", methods=["POST"])
def montar_video():
    from difflib import SequenceMatcher

    slug      = (request.get_json() or {}).get("slug")
    folder_id = (request.get_json() or {}).get("folder_id")
    if not slug or not folder_id:
        return jsonify({"error":"slug e folder_id obrigatórios"}),400

    base  = FILES_DIR/slug
    imgs  = sorted(base.glob("*.*"))
    if not imgs: return jsonify({"error":"nenhuma imagem selecionada"}),400

    # único MP3, SRT, CSV
    mp3 = next(AUDIO_DIR.glob("*.mp3"),None)
    srt = next(FILES_DIR.glob("*.srt"),None)
    if not mp3 or not srt: return jsonify({"error":"mp3 ou srt ausente"}),400

    # lê transcrição
    trans=[]
    with open(srt,encoding="utf-8") as f:
        for bloco in f.read().split("\n\n"):
            l=bloco.split("\n")
            if len(l)>=3:
                a,b = l[1].split(" --> ")
                t   = " ".join(l[2:])
                trans.append({
                    "inicio": sum(float(x)*60**i for i,x in enumerate(reversed(a.replace(",",".").split(":")))),
                    "fim":   sum(float(x)*60**i for i,x in enumerate(reversed(b.replace(",",".").split(":")))),
                    "texto":t
                })

    audio_clip = AudioFileClip(str(mp3))
    clips=[]

    for idx,seg in enumerate(trans):
        dur  = seg["fim"]-seg["inicio"]
        txt  = seg["texto"]
        imgc = ImageClip(str(imgs[idx%len(imgs)])).resize(height=720).crop(x_center='center',width=1280).set_duration(dur)
        zoom = imgc.resize(lambda t:1+0.02*t)
        leg  = TextClip(txt.upper(),fontsize=60,font="DejaVu-Sans-Bold",
                        stroke_color="black",stroke_width=2,size=(1280,None),
                        method="caption").set_duration(dur).set_position(("center","bottom"))
        grain= make_grain().set_opacity(0.05).set_duration(dur)
        luz  = VideoFileClip("sobrepor.mp4").resize((1280,720)).set_opacity(0.07).set_duration(dur)
        marca= ImageClip("sobrepor.png").resize(height=100).set_position((20,20)).set_duration(dur)
        clips.append(CompositeVideoClip([zoom,grain,luz,marca,leg],size=(1280,720)))

    # encerramento
    end_img  = ImageClip("fechamento.png").resize(height=720).crop(x_center='center',width=1280).set_duration(3)
    end_luz  = VideoFileClip("sobrepor.mp4").resize((1280,720)).set_opacity(0.07).set_duration(3)
    end_grain= make_grain().set_opacity(0.05).set_duration(3)
    end_clip = CompositeVideoClip([end_img,end_grain,end_luz],size=(1280,720))

    final = concatenate_videoclips(clips+[end_clip]).set_audio(audio_clip)
    output= FILES_DIR/f"{slug}.mp4"
    final.write_videofile(str(output),fps=24,codec="libx264",audio_codec="aac")

    # envia
    drv = get_drive_service()
    upload_arquivo_drive(output,"video_final.mp4",folder_id,drv)
    return jsonify({"ok":True,"url":f"https://drive.google.com/drive/folders/{folder_id}"})

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)