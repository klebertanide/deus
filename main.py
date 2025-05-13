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

# Diretórios locais
BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR = BASE / "csv"
FILES_DIR = BASE / "downloads"
for d in [AUDIO_DIR, CSV_DIR, FILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Credenciais
GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
openai.api_key = os.getenv("OPENAI_API_KEY")

# Utilitários
def slugify(texto, limite=30):
    s = unidecode.unidecode(texto)
    s = re.sub(r"[^\w\s]", "", s).strip().replace(" ", "_")
    return s[:limite].lower()

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_pasta_drive(slug, drive):
    meta = {"name": slug, "mimeType": "application/vnd.google-apps.folder", "parents":[GOOGLE_DRIVE_FOLDER_ID]}
    pasta = drive.files().create(body=meta, fields="id").execute()
    return pasta["id"]

def upload_arquivo_drive(path, nome, fid, drive):
    meta = {"name": nome, "parents":[fid]}
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(body=meta, media_body=media, fields="id").execute()

def format_ts(sec):
    ms = int((sec%1)*1000)
    h = int(sec//3600); m = int((sec%3600)//60); s = int(sec%60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280,720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity,128+intensity,(size[1],size[0],1),dtype=np.uint8)
        return np.repeat(noise,3,axis=2)
    return VideoClip(frame, duration=1).set_fps(24)

# 1) /falar → gera MP3
@app.route("/falar", methods=["POST"])
def falar():
    txt = (request.get_json() or {}).get("texto","").strip()
    if not txt:
        return jsonify(error="campo 'texto' obrigatório"),400
    slug = slugify(txt)
    nome = f"{slug}.mp3"; path = AUDIO_DIR/nome
    # ElevenLabs TTS
    def req(payload):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{payload['voice_id']}/stream"
        hdr = {"xi-api-key":ELEVEN_API_KEY,"Content-Type":"application/json"}
        r = requests.post(url, headers=hdr, json=payload["body"], stream=True, timeout=60)
        r.raise_for_status(); return r.content
    # tenta com style, se falhar sem style
    body1={"text":txt,"voice_settings":{"stability":0.6,"similarity_boost":0.9,"style":0.2}}
    try:
        audio = req({"voice_id":"cwIsrQsWEVTols6slKYN","body":body1})
    except:
        body2={"text":txt,"voice_settings":{"stability":0.6,"similarity_boost":0.9}}
        audio = req({"voice_id":"cwIsrQsWEVTols6slKYN","body":body2})
    with open(path,"wb") as f: f.write(audio)
    return jsonify(audio_url=request.url_root.rstrip("/")+f"/audio/{nome}", filename=nome, slug=slug)

# 2) /transcrever → Whisper verbose_json
@app.route("/transcrever", methods=["POST"])
def transcrever():
    url = (request.get_json() or {}).get("audio_url","").strip()
    if not url:
        return jsonify(error="campo 'audio_url' obrigatório"),400
    # baixa ou abre local
    if url.startswith(request.url_root.rstrip("/")):
        fn = url.rsplit("/audio/",1)[-1]; af = open(AUDIO_DIR/fn,"rb")
    else:
        r = requests.get(url,timeout=60); r.raise_for_status()
        af = io.BytesIO(r.content); af.name="audio.mp3"
    try:
        tr = openai.audio.transcriptions.create(
            model="whisper-1", file=af,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )
        segments = [{"inicio":s["start"],"fim":s["end"],"texto":s["text"].strip()}
                    for s in tr["segments"]]
        return jsonify(duracao_total=tr["duration"], transcricao=segments)
    except Exception as e:
        return jsonify(error=str(e)),500
    finally:
        af.close()

# 3) /gerar_csv → CSV, SRT, TXT e upload drive
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    js = request.get_json() or {}
    trs = js.get("transcricao",[])
    pr = js.get("prompts",[])
    desc = js.get("descricao","")
    if not trs or not pr or len(trs)!=len(pr):
        return jsonify(error="transcricao+prompts inválidos"),400
    # detecta mp3 único se necessário
    mp3 = js.get("mp3_filename") or next((p.name for p in AUDIO_DIR.glob("*.mp3")),None)
    if not mp3:
        return jsonify(error="MP3 não encontrado"),400
    slug = js.get("slug") or Path(mp3).stem
    drive = get_drive_service(); fid = criar_pasta_drive(slug,drive)
    # CSV
    csvp=CSV_DIR/f"{slug}.csv"; srtp=FILES_DIR/f"{slug}.srt"; txtp=FILES_DIR/f"{slug}.txt"
    header=["PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL","SEED_NUMBER",
            "RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"]
    neg="low quality,overexposed,underexposed,extra limbs,bad anatomy"
    with open(csvp,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(header)
        for seg,p in zip(trs,pr):
            sec=int(round(seg["inicio"]))
            prompt=f"{sec} - Painting style: Traditional watercolor, soft brush. {p}"
            w.writerow([prompt,"PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])
    # SRT
    with open(srtp,"w",encoding="utf-8") as f:
        for i,seg in enumerate(trs,1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")
    # TXT
    with open(txtp,"w",encoding="utf-8") as f:
        f.write(desc.strip())
    # upload
    upload_arquivo_drive(csvp,"imagens.csv",fid,drive)
    upload_arquivo_drive(srtp,"legenda.srt",fid,drive)
    upload_arquivo_drive(txtp,"descricao.txt",fid,drive)
    upload_arquivo_drive(AUDIO_DIR/mp3,"voz.mp3",fid,drive)
    return jsonify(folder_url=f"https://drive.google.com/drive/folders/{fid}")

# 4) /upload_zip → extrai e seleciona imagens
@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    f = request.files.get("zip")
    if not f:
        return jsonify(error="Campo 'zip' obrigatório."),400
    # detecta pasta projeto única
    pats=[p for p in FILES_DIR.iterdir() if p.is_dir() and not p.name.endswith("_raw")]
    if len(pats)!=1:
        return jsonify(error="pasta de projeto ambígua"),400
    slug=pats[0].name
    tmp=FILES_DIR/f"{slug}_raw"; out=FILES_DIR/slug
    tmp.mkdir(exist_ok=True); out.mkdir(exist_ok=True)
    zf=tmp/"imgs.zip"; f.save(zf)
    with zipfile.ZipFile(zf) as z: z.extractall(tmp)
    imgs=[p for p in tmp.glob("*.*") if p.suffix.lower() in [".jpg",".jpeg",".png"]]
    if not imgs:
        return jsonify(error="Nenhuma imagem"),400
    # lê prompts
    csvp=CSV_DIR/f"{slug}.csv"
    if not csvp.exists():
        return jsonify(error="CSV faltando"),400
    prompts=[row["PROMPT"].split(" - ",1)[-1].strip()
             for row in csv.DictReader(open(csvp,encoding="utf-8"))]
    # CLIP só nome→embedding
    from sentence_transformers import SentenceTransformer, util
    clip = SentenceTransformer("clip-ViT-B-32")
    res=[]
    for idx,p in enumerate(prompts):
        e_txt=clip.encode(p,convert_to_tensor=True)
        best,max_score=None,-1
        for img in imgs:
            name= re.sub(r"[^\w\s]"," ",img.stem)
            e_img=clip.encode(name,convert_to_tensor=True)
            sc=float(util.cos_sim(e_txt,e_img))
            if sc>max_score:
                max_score, best = sc, img
        dst=out/f"{idx:02d}_{best.name}"
        best.rename(dst); imgs.remove(best); res.append(dst.name)
    return jsonify(ok=True,slug=slug,usadas=res)

# 5) /montar_video → cria o vídeo final
@app.route("/montar_video", methods=["POST"])
def montar_video():
    data=request.get_json() or {}
    slug=data.get("slug")
    folder_id=data.get("folder_id")
    # procura imagens, mp3, srt, csv
    imgdir=FILES_DIR/slug
    imgs=sorted([p for p in imgdir.iterdir() if p.suffix.lower() in [".jpg",".png"]])
    mp3=list(AUDIO_DIR.glob("*.mp3")); srt=list(FILES_DIR.glob("*.srt"))
    csvs=list(CSV_DIR.glob("*.csv"))
    if not (imgs and mp3 and srt and csvs):
        return jsonify(error="arquivos faltando"),400
    audio_path=mp3[0]; srt_path=srt[0]; csv_path=csvs[0]
    # lê prompts
    prompts=[row[0].split(" - ",1)[-1] for row in csv.reader(open(csv_path,encoding="utf-8")) if row][1:]
    # lê transcrição SRT
    trans=[]
    for blk in open(srt_path,encoding="utf-8").read().strip().split("\n\n"):
        L=blk.split("\n")
        if len(L)>=3:
            st,et=L[1].split(" --> ")
            def p(t): h,m,r=t.replace(",",".").split(":"); return int(h)*3600+int(m)*60+float(r)
            trans.append({"inicio":p(st),"fim":p(et),"texto":" ".join(L[2:])})
    audio_clip=AudioFileClip(str(audio_path)); clips=[]
    for i,seg in enumerate(trans):
        dur=seg["fim"]-seg["inicio"]; txt=seg["texto"]
        img=ImageClip(str(imgs[i%len(imgs)])).resize(height=720).crop(x_center='center',width=1280).set_duration(dur)
        zoom=img.resize(lambda t:1+0.02*t)
        legend=TextClip(txt.upper(),fontsize=60,font="DejaVu-Sans-Bold",
                        color="white",stroke_color="black",stroke_width=2,
                        size=(1280,None),method="caption"
                       ).set_duration(dur).set_position(("center","bottom"))
        grain=make_grain().set_opacity(0.05).set_duration(dur)
        luz=VideoFileClip("sobrepor.mp4").resize((1280,720)).set_opacity(0.07).set_duration(dur)
        marca=ImageClip("sobrepor.png").resize(height=100).set_position((20,20)).set_opacity(1).set_duration(dur)
        clips.append(CompositeVideoClip([zoom,grain,luz,marca,legend],size=(1280,720)))
    # encerramento
    fim=CompositeVideoClip([
        ImageClip("fechamento.png").resize(height=720).crop(x_center='center',width=1280),
        make_grain().set_opacity(0.05).set_duration(3),
        VideoFileClip("sobrepor.mp4").resize((1280,720)).set_opacity(0.07).set_duration(3)
    ],size=(1280,720)).set_duration(3)
    final=concatenate_videoclips(clips+[fim]).set_audio(audio_clip)
    outp=FILES_DIR/f"{slug}.mp4"
    final.write_videofile(str(outp),fps=24,codec="libx264",audio_codec="aac")
    upload_arquivo_drive(outp,"video_final.mp4",folder_id,get_drive_service())
    return jsonify(ok=True,video=f"https://drive.google.com/drive/folders/{folder_id}")

# Rotas estáticas e plugin
@app.route("/audio/<path:fn>")       def serve_a(fn): return send_from_directory(AUDIO_DIR, fn)
@app.route("/csv/<path:fn>")         def serve_c(fn): return send_from_directory(CSV_DIR, fn)
@app.route("/downloads/<path:fn>")   def serve_d(fn): return send_from_directory(FILES_DIR, fn)
@app.route("/.well-known/openapi.json")   def oai(): return send_from_directory(".well-known","openapi.json",mimetype="application/json")
@app.route("/.well-known/ai-plugin.json") def aip(): return send_from_directory(".well-known","ai-plugin.json",mimetype="application/json")

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)