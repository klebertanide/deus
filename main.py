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

# Pastas locais
BASE        = Path(".")
AUDIO_DIR   = BASE / "audio"
CSV_DIR     = BASE / "csv"
FILES_DIR   = BASE / "downloads"
for d in [AUDIO_DIR, CSV_DIR, FILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Drive & chaves
GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY        = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY            = os.getenv("OPENAI_API_KEY")
openai.api_key        = OPENAI_KEY

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(texto, limite=30):
    texto = unidecode.unidecode(texto)
    texto = re.sub(r"(?i)^deus\s+", "", texto)
    texto = re.sub(r"[^\w\s]", "", texto)
    texto = texto.strip().replace(" ", "_")
    return texto[:limite].lower()

def criar_pasta_drive(slug, drive):
    meta = {"name": slug, "mimeType": "application/vnd.google-apps.folder",
            "parents": [GOOGLE_DRIVE_FOLDER_ID]}
    pasta = drive.files().create(body=meta, fields="id").execute()
    return pasta.get("id")

def upload_arquivo_drive(filepath, filename, folder_id, drive):
    meta  = {"name": filename, "parents": [folder_id]}
    media = MediaFileUpload(str(filepath), resumable=True)
    f     = drive.files().create(body=meta, media_body=media, fields="id").execute()
    return f.get("id")

def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280,720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity,128+intensity,(size[1],size[0],1),dtype=np.uint8)
        return np.repeat(noise,3,axis=2)
    return VideoClip(frame, duration=1).set_fps(24)

def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN", retries=3):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    def post(payload):
        for i in range(retries):
            resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
            if resp.ok:
                return resp.content
            time.sleep(2**i)
        resp.raise_for_status()

    p1 = {"text": text, "voice_settings": {"stability":0.6,"similarity_boost":0.9,"style":0.2}}
    try:
        return post(p1)
    except:
        p2 = {"text": text, "voice_settings": {"stability":0.6,"similarity_boost":0.9}}
        return post(p2)

@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

@app.route("/audio/<path:fn>")
def servir_audio(fn):
    return send_from_directory(AUDIO_DIR, fn)

@app.route("/csv/<path:fn>")
def servir_csv(fn):
    return send_from_directory(CSV_DIR, fn)

@app.route("/downloads/<path:fn>")
def servir_down(fn):
    return send_from_directory(FILES_DIR, fn)

# ─── /falar ─────────────────────────────────────────────────────────────────────
@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify({"error":"campo 'texto' obrigatório"}),400

    slug     = slugify(texto)
    filename = f"{slug}.mp3"
    path     = AUDIO_DIR / filename

    try:
        audio = elevenlabs_tts(texto)
    except Exception as e:
        return jsonify({"error":"falha ElevenLabs","detalhe":str(e)}),500

    with open(path,"wb") as f:
        f.write(audio)

    return jsonify({
        "audio_url": request.url_root.rstrip("/") + f"/audio/{filename}",
        "filename": filename,
        "slug": slug
    })

# ─── /transcrever ────────────────────────────────────────────────────────────────
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error":"campo 'audio_url' obrigatório"}),400

    try:
        if audio_url.startswith(request.url_root.rstrip("/")):
            fname      = audio_url.rsplit("/audio/",1)[-1]
            audio_file = open(AUDIO_DIR/fname,"rb")
        else:
            r = requests.get(audio_url,timeout=60); r.raise_for_status()
            audio_file = io.BytesIO(r.content); audio_file.name="audio.mp3"

        # gera SRT
        srt_text = openai.audio.transcriptions.create(
            model="whisper-1", file=audio_file, response_format="srt"
        )
        def parse_ts(ts):
            h,m,rest = ts.split(":")
            s,ms     = rest.split(",")
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

        segments=[]
        for block in srt_text.strip().split("\n\n"):
            lines = block.split("\n")
            if len(lines)>=3:
                start,end = lines[1].split(" --> ")
                text = " ".join(lines[2:])
                segments.append({
                    "inicio": parse_ts(start),
                    "fim":    parse_ts(end),
                    "texto":  text
                })

        return jsonify({
            "duracao_total": segments[-1]["fim"],
            "transcricao":   segments
        })

    except Exception as e:
        return jsonify({"error": str(e)}),500
    finally:
        try: audio_file.close()
        except: pass

# ─── /gerar_csv ──────────────────────────────────────────────────────────────────
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data         = request.get_json() or {}
    transcricao  = data.get("transcricao", [])
    prompts      = data.get("prompts", [])
    descricao    = data.get("descricao", "")
    mp3_filename = data.get("mp3_filename")

    # auto-detect MP3
    if not mp3_filename:
        mp3s = list(AUDIO_DIR.glob("*.mp3"))
        if len(mp3s)==1:
            mp3_filename = mp3s[0].name
        elif not mp3s:
            return jsonify({"error":"Nenhum arquivo .mp3 encontrado."}),400
        else:
            return jsonify({"error":"Vários .mp3 encontrados. Informe 'mp3_filename'."}),400

    slug = data.get("slug", Path(mp3_filename).stem)

    if not transcricao or not prompts or len(transcricao)!=len(prompts):
        return jsonify({"error":"transcricao+prompts inválidos"}),400

    mp3_path = AUDIO_DIR/mp3_filename
    if not mp3_path.exists():
        return jsonify({"error":"MP3 não encontrado"}),400

    drive    = get_drive_service()
    pasta_id = criar_pasta_drive(slug, drive)

    csv_path = CSV_DIR/f"{slug}.csv"
    srt_path = FILES_DIR/f"{slug}.srt"
    txt_path = FILES_DIR/f"{slug}.txt"

    # CSV
    header = [
        "PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL",
        "SEED_NUMBER","RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"
    ]
    neg = ("low quality, overexposed, underexposed, extra limbs, extra fingers, "
           "missing fingers, disfigured, deformed, bad anatomy")
    with open(csv_path,"w",newline="",encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(header)
        for seg,p in zip(transcricao,prompts):
            sec = int(round(seg["inicio"]))
            pf  = (f"{sec} - Painting style: Traditional watercolor, "
                   f"with soft brush strokes and handmade paper texture. {p}")
            w.writerow([pf,"PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT
    with open(srt_path,"w",encoding="utf-8") as s:
        for i,seg in enumerate(transcricao,1):
            s.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n")
            s.write(f"{seg['texto'].strip()}\n\n")

    # TXT
    with open(txt_path,"w",encoding="utf-8") as t:
        t.write(descricao.strip())

    # upload
    upload_arquivo_drive(csv_path, f"{slug}.csv", pasta_id, drive)
    upload_arquivo_drive(srt_path, f"{slug}.srt", pasta_id, drive)
    upload_arquivo_drive(txt_path, f"{slug}.txt", pasta_id, drive)
    upload_arquivo_drive(mp3_path, f"{slug}.mp3", pasta_id, drive)

    return jsonify({"folder_url":f"https://drive.google.com/drive/folders/{pasta_id}"})

# ─── /upload_zip ────────────────────────────────────────────────────────────────
@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify({"error":"Campo 'zip' obrigatório."}),400

    projetos = [p for p in FILES_DIR.iterdir() if p.is_dir() and not p.name.endswith("_raw")]
    if len(projetos)==0:
        return jsonify({"error":"Nenhuma pasta de projeto encontrada."}),400
    if len(projetos)>1:
        return jsonify({"error":"Mais de uma pasta encontrada. Especifique."}),400

    slug       = projetos[0].name
    temp_dir   = FILES_DIR/f"{slug}_raw"
    output_dir = FILES_DIR/slug
    temp_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    zip_path = temp_dir/"imagens.zip"
    file.save(zip_path)
    with zipfile.ZipFile(zip_path,'r') as z:
        z.extractall(temp_dir)

    imagens = [f for f in temp_dir.glob("*.*") if f.suffix.lower() in {".jpg",".jpeg",".png"}]
    if not imagens:
        return jsonify({"error":"Nenhuma imagem no ZIP."}),400

    # lê prompts do CSV
    csvp = CSV_DIR/f"{slug}.csv"
    if not csvp.exists():
        return jsonify({"error":"CSV não encontrado para este projeto."}),400

    prompts=[]
    with open(csvp,newline='',encoding='utf-8') as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            prompts.append(row["PROMPT"].split(" - ",1)[-1].strip())

    # similaridade simples (stem vs prompt)
    from difflib import SequenceMatcher
    def sim(a,b): return SequenceMatcher(None,a.lower(),b.lower()).ratio()

    usadas=[]
    for i,p in enumerate(prompts):
        best = max(imagens, key=lambda img: sim(p,img.stem))
        dest = output_dir/f"{i:02d}_{best.name}"
        best.rename(dest)
        imagens.remove(best)
        usadas.append(dest.name)

    return jsonify({"ok":True,"slug":slug,"usadas":usadas})

# ─── /montar_video ───────────────────────────────────────────────────────────────
@app.route("/montar_video", methods=["POST"])
def montar_video():
    from difflib import SequenceMatcher
    def sim(a,b): return SequenceMatcher(None,a.lower(),b.lower()).ratio()

    data      = request.get_json(force=True)
    slug      = data.get("slug")
    folder_id = data.get("folder_id")

    pasta = FILES_DIR/slug
    imgs  = sorted(p for p in pasta.iterdir() if p.suffix.lower() in {".jpg",".jpeg",".png"})
    if not imgs:
        return jsonify({"error":"Nenhuma imagem encontrada."}),400

    mp3s = list(AUDIO_DIR.glob("*.mp3"))
    if not mp3s:
        return jsonify({"error":"Nenhum áudio encontrado."}),400
    audio_path = mp3s[0]

    srt_files = list(FILES_DIR.glob("*.srt"))
    if not srt_files:
        return jsonify({"error":"Nenhuma legenda .srt encontrada."}),400
    srt_path = srt_files[0]

    csvs = list(CSV_DIR.glob("*.csv"))
    if not csvs:
        return jsonify({"error":"Nenhum CSV encontrado."}),400
    csvp = csvs[0]

    prompts=[]
    with open(csvp,newline='',encoding='utf-8') as f:
        rdr = csv.reader(f); next(rdr)
        for row in rdr:
            prompts.append(row[0].split(" - ",1)[-1])

    # associa em ordem
    assoc,used = [],set()
    for p in prompts:
        best = max([i for i in imgs if i not in used], key=lambda x: sim(p,x.stem), default=None)
        if best:
            assoc.append(best); used.add(best)
        else:
            assoc.append(imgs[0])

    # parse SRT
    with open(srt_path,encoding='utf-8') as f:
        bloco = f.read().strip().split("\n\n")
    trans=[]
    for b in bloco:
        ln = b.split("\n")
        if len(ln)>=3:
            t0,t1 = ln[1].split(" --> ")
            def p(ts):
                h,m,r=ts.split(":"); s,ms=r.split(",")
                return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
            trans.append({"inicio":p(t0),"fim":p(t1),"texto":" ".join(ln[2:])})

    audio_clip = AudioFileClip(str(audio_path))
    clips=[]

    for idx,seg in enumerate(trans):
        dur   = seg["fim"]-seg["inicio"]
        img_c = ImageClip(str(assoc[idx%len(assoc)])).resize(height=720).crop(x_center='center',width=1280).set_duration(dur)
        zoom  = img_c.resize(lambda t:1+0.02*t)
        legend= TextClip(seg["texto"].upper(), fontsize=60, font='DejaVu-Sans-Bold',
                         color='white',stroke_color='black',stroke_width=2,
                         size=(1280,None), method='caption')\
                .set_duration(dur).set_position(('center','bottom'))
        grain = make_grain().set_opacity(0.05).set_duration(dur)
        comp  = CompositeVideoClip([zoom,grain,legend], size=(1280,720))
        clips.append(comp)

    fim_img = ImageClip("fechamento.png").resize(height=720).crop(x_center='center',width=1280).set_duration(3)
    final  = concatenate_videoclips(clips+[fim_img]).set_audio(audio_clip)
    out    = FILES_DIR/f"{slug}.mp4"
    final.write_videofile(str(out), fps=24, codec='libx264', audio_codec='aac')

    drive = get_drive_service()
    upload_arquivo_drive(out, "video_final.mp4", folder_id, drive)

    return jsonify({"ok":True,"video":f"https://drive.google.com/drive/folders/{folder_id}"})

# serve plugin & openapi
@app.route('/.well-known/ai-plugin.json')
def serve_ai_plugin():
    return send_from_directory('.well-known','ai-plugin.json',mimetype='application/json')

@app.route('/.well-known/openapi.json')
def serve_openapi():
    return send_from_directory('.well-known','openapi.json',mimetype='application/json')

if __name__ == "__main__":
    port = int(os.getenv("PORT",5000))
    app.run(host="0.0.0.0",port=port,debug=True)
