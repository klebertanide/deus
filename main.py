# main.py
import os
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
BASE       = Path(".")
AUDIO_DIR  = BASE / "audio"
CSV_DIR    = BASE / "csv"
FILES_DIR  = BASE / "downloads"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR):
    d.mkdir(exist_ok=True)

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

def slugify(texto, limite=30):
    s = unidecode.unidecode(texto)
    s = re.sub(r"[^\w\s]", "", s).strip().replace(" ", "_")
    return s[:limite].lower()

def criar_pasta_drive(slug, drive):
    meta = {"name": slug, "mimeType": "application/vnd.google-apps.folder", "parents":[GOOGLE_DRIVE_FOLDER_ID]}
    pasta = drive.files().create(body=meta, fields="id").execute()
    return pasta["id"]

def upload_arquivo_drive(path, nome, folder_id, drive):
    meta  = {"name": nome, "parents":[folder_id]}
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(body=meta, media_body=media, fields="id").execute()

def format_ts(s):
    ms = int((s%1)*1000)
    h  = int(s//3600); m = int((s%3600)//60); sec = int(s%60)
    return f"{h:02}:{m:02}:{sec:02},{ms:03}"

def make_grain(size=(1280,720), intensity=10):
    def f(t):
        n = np.random.randint(128-intensity,128+intensity,(size[1],size[0],1),np.uint8)
        return np.repeat(n,3,axis=2)
    return VideoClip(f, duration=1).set_fps(24)

@app.route("/")
def home():
    return "API OK"

@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json(force=True)
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"),400

    slug     = slugify(texto)
    mp3_path = AUDIO_DIR / f"{slug}.mp3"

    # ElevenLabs TTS
    payload = {"text": texto, "voice_settings":{"stability":0.6,"similarity_boost":0.9,"style":0.2}}
    headers = {"xi-api-key":ELEVEN_API_KEY,"Content-Type":"application/json"}
    resp = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{data.get('voice_id','cwIsrQsWEVTols6slKYN')}/stream",
                         json=payload, headers=headers, stream=True, timeout=60)
    resp.raise_for_status()
    with open(mp3_path,"wb") as f: f.write(resp.content)

    return jsonify(slug=slug,
                   audio_url=f"{request.url_root}audio/{slug}.mp3"),200

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json(force=True)
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"),400

    # baixa ou abre local
    if audio_url.startswith(request.url_root):
        fname = audio_url.rsplit("/",1)[-1]
        fobj  = open(AUDIO_DIR/fname,"rb")
    else:
        r = requests.get(audio_url,timeout=60); r.raise_for_status()
        fobj = io.BytesIO(r.content); fobj.name="audio.mp3"

    # gera SRT
    srt = openai.audio.transcriptions.create(
        model="whisper-1", file=fobj, response_format="srt"
    )

    # parse SRT
    def p_ts(t): h,m,rest=t.split(":"); s,ms=rest.split(","); return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
    segs=[]
    for blk in srt.strip().split("\n\n"):
        lines=blk.split("\n")
        if len(lines)>=3:
            start,end=lines[1].split(" --> ")
            txt=" ".join(lines[2:])
            segs.append({"inicio":p_ts(start),"fim":p_ts(end),"texto":txt})

    fobj.close()
    return jsonify(duracao_total=segs[-1]["fim"],transcricao=segs),200

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data        = request.get_json(force=True)
    slug        = data.get("slug")
    trans       = data.get("transcricao",[])
    prompts     = data.get("prompts",[])
    descricao   = data.get("descricao","")
    if not slug:
        return jsonify(error="campo 'slug' obrigatório"),400
    if not trans or not prompts or len(trans)!=len(prompts):
        return jsonify(error="transcricao+prompts inválidos"),400

    # paths
    mp3 = AUDIO_DIR/f"{slug}.mp3"
    c   = CSV_DIR  /f"{slug}.csv"
    s   = FILES_DIR/f"{slug}.srt"
    t   = FILES_DIR/f"{slug}.txt"
    drive = get_drive_service()
    pid   = criar_pasta_drive(slug, drive)

    # CSV
    hdr = ["TIME","PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL","RENDERING","NEGATIVE_PROMPT","STYLE"]
    neg = "low quality,overexposed,underexposed"
    with open(c,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(hdr)
        for seg,p in zip(trans,prompts):
            w.writerow([int(seg["inicio"]),p,"PRIVATE","9:16","ON","3.0","TURBO",neg,"AUTO"])
    # SRT
    with open(s,"w",encoding="utf-8") as f:
        for i,seg in enumerate(trans,1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")
    # TXT
    with open(t,"w",encoding="utf-8") as f: f.write(descricao)

    # upload
    upload_arquivo_drive(c, f"{slug}.csv", pid, drive)
    upload_arquivo_drive(s, f"{slug}.srt", pid, drive)
    upload_arquivo_drive(t, f"{slug}.txt", pid, drive)
    upload_arquivo_drive(mp3, f"{slug}.mp3", pid, drive)

    return jsonify(folder_id=pid,folder_url=f"https://drive.google.com/drive/folders/{pid}"),200

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    f = request.files.get("zip")
    if not f:
        return jsonify(error="Campo 'zip' obrigatório"),400
    # slug único do CSV
    csvs = list(CSV_DIR.glob("*.csv"))
    if not csvs:
        return jsonify(error="Nenhum CSV encontrado"),400
    slug = csvs[0].stem
    tmp  = FILES_DIR/f"{slug}_raw"; out=FILES_DIR/slug
    tmp.mkdir(exist_ok=True); out.mkdir(exist_ok=True)
    path = tmp/"imgs.zip"; f.save(path)
    with zipfile.ZipFile(path,"r") as z: z.extractall(tmp)
    imgs=[p for p in tmp.iterdir() if p.suffix.lower() in (".jpg",".jpeg",".png")]
    if not imgs:
        return jsonify(error="Nenhuma imagem no ZIP"),400

    # lê prompts
    prm=[]
    with open(CSV_DIR/f"{slug}.csv",newline="",encoding="utf-8") as f:
        r=csv.DictReader(f)
        for row in r: prm.append(row["PROMPT"])

    selecionadas=[]
    from sentence_transformers import SentenceTransformer, util
    model = SentenceTransformer("clip-ViT-B-32")
    for text in prm:
        emb_t = model.encode(text,convert_to_tensor=True)
        best,score=None,-1
        for img in imgs:
            nm=re.sub(r"[^\w\s]"," ",img.stem)
            emb_i=model.encode(nm,convert_to_tensor=True)
            s = util.cos_sim(emb_t,emb_i).item()
            if s>score:
                best,score=img,s
        if best:
            dst=out/best.name
            best.replace(dst)
            selecionadas.append(best.name)
            imgs.remove(best)

    return jsonify(slug=slug,selecionadas=selecionadas),200

@app.route("/montar_video", methods=["POST"])
def montar_video():
    data      = request.get_json(force=True)
    pid       = data.get("folder_id")
    if not pid:
        return jsonify(error="campo 'folder_id' obrigatório"),400
    # slug
    slug = list(CSV_DIR.glob("*.csv"))[0].stem
    # arquivos
    mp3 = AUDIO_DIR/f"{slug}.mp3"; srt=list(FILES_DIR.glob(f"{slug}.srt"))[0]
    imgs=sorted((FILES_DIR/slug).glob("*.*"))
    # parse SRT
    with open(srt,encoding="utf-8") as f: blocks=f.read().strip().split("\n\n")
    trans=[]
    for b in blocks:
        l=b.split("\n")
        if len(l)>=3:
            a,e=l[1].split(" --> ")
            def ts(t): h,m,rest=t.split(":");s,ms=rest.split(",");return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
            trans.append({"inicio":ts(a),"fim":ts(e),"texto":" ".join(l[2:])})

    audio_clip=AudioFileClip(str(mp3))
    clips=[]
    for i,seg in enumerate(trans):
        dur=seg["fim"]-seg["inicio"]
        img=ImageClip(str(imgs[i%len(imgs)])).set_duration(dur).resize(height=720).crop(x_center="center",width=1280)
        txt=TextClip(seg["texto"],fontsize=60,method="caption",size=(1280,None),
                     color="white",stroke_color="black",stroke_width=2)\
            .set_position(("center","bottom")).set_duration(dur)
        comp=CompositeVideoClip([img,make_grain().set_opacity(0.05),txt],size=(1280,720))
        clips.append(comp)
    final=concatenate_videoclips(clips).set_audio(audio_clip)
    outp=FILES_DIR/f"{slug}.mp4"
    final.write_videofile(str(outp),fps=24,codec="libx264",audio_codec="aac")

    drive=get_drive_service()
    upload_arquivo_drive(outp,f"{slug}.mp4",pid,drive)
    return jsonify(video_url=f"https://drive.google.com/drive/folders/{pid}"),200

# estáticos e plugin
@app.route("/audio/<path:fn>")       def serve_a(fn): return send_from_directory(AUDIO_DIR,fn)
@app.route("/csv/<path:fn>")         def serve_c(fn): return send_from_directory(CSV_DIR,fn)
@app.route("/downloads/<path:fn>")   def serve_d(fn): return send_from_directory(FILES_DIR,fn)
@app.route("/.well-known/openapi.json") def oapi(): return send_from_directory(".well-known","openapi.json",mimetype="application/json")
@app.route("/.well-known/ai-plugin.json") def ap(): return send_from_directory(".well-known","ai-plugin.json",mimetype="application/json")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
