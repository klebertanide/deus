# main.py
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
    VideoFileClip, VideoClip
)
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY        = os.getenv("ELEVENLABS_API_KEY")
openai.api_key        = os.getenv("OPENAI_API_KEY")

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_pasta_drive(nome, drive):
    meta = {
        "name": nome,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [GOOGLE_DRIVE_FOLDER_ID]
    }
    folder = drive.files().create(body=meta, fields="id").execute()
    return folder["id"]

def upload_drive(path, name, folder_id, drive):
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(
        body={"name": name, "parents":[folder_id]},
        media_body=media
    ).execute()

def slugify(text, limit=30):
    txt = unidecode.unidecode(text)
    txt = re.sub(r"[^\w\s]", "", txt)
    return txt.strip().replace(" ", "_").lower()[:limit]

def format_ts(sec):
    ms = int((sec%1)*1000)
    h  = int(sec//3600)
    m  = int((sec%3600)//60)
    s  = int(sec%60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280,720), intensity=10):
    def frame(t):
        noise = np.random.randint(
            128-intensity,128+intensity,
            (size[1],size[0],1),dtype=np.uint8
        )
        return np.repeat(noise,3,axis=2)
    return VideoClip(frame,duration=1).set_fps(24)

@app.route("/")
def home():
    return "OK"

@app.route("/project/<slug>/<path:fn>")
def serve_file(slug, fn):
    return send_from_directory(slug, fn)

@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="texto obrigatório"),400

    slug     = slugify(texto)
    proj_dir = Path(slug)
    proj_dir.mkdir(exist_ok=True)
    mp3_path = proj_dir/f"{slug}.mp3"

    # ElevenLabs TTS
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
        headers={"xi-api-key":ELEVEN_API_KEY},
        json={"text":texto,"voice_settings":{"stability":0.6,"similarity_boost":0.9},"model_id":"eleven_multilingual_v2","voice_id":"cwIsrQsWEVTols6slKYN"}
    )
    r.raise_for_status()
    mp3_path.write_bytes(r.content)

    return jsonify(
        slug      = slug,
        audio_url = f"{request.url_root}project/{slug}/{slug}.mp3"
    )

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data  = request.get_json() or {}
    slug  = data.get("slug")
    if not slug:
        return jsonify(error="slug obrigatório"),400
    proj_dir = Path(slug)
    mp3_path = proj_dir/f"{slug}.mp3"
    if not mp3_path.exists():
        return jsonify(error="MP3 não encontrado"),404

    with open(mp3_path,"rb") as f:
        srt = openai.audio.transcriptions.create(
            model="whisper-1", file=f, response_format="srt"
        )
    segs=[]
    for blk in srt.strip().split("\n\n"):
        lines=blk.split("\n")
        if len(lines)<3: continue
        st,en=lines[1].split(" --> ")
        txt=" ".join(lines[2:])
        def p(ts):
            h,m,rest=ts.split(":"); s,ms=rest.split(",")
            return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
        segs.append({"inicio":p(st),"fim":p(en),"texto":txt})
    return jsonify(transcricao=segs)

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data       = request.get_json() or {}
    slug       = data.get("slug")
    trans      = data.get("transcricao",[])
    prompts    = data.get("prompts",[])
    descricao  = data.get("descricao","")
    if not slug or not trans or len(trans)!=len(prompts):
        return jsonify(error="dados inválidos"),400

    proj_dir = Path(slug)
    proj_dir.mkdir(exist_ok=True)
    drive    = get_drive_service()
    folderId = criar_pasta_drive(slug, drive)

    # CSV, SRT, TXT
    with open(proj_dir/f"{slug}.csv","w",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        w.writerow(["TIME","PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL","SEED","RENDERING","NEGATIVE","STYLE","PALETTE"])
        neg="low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy"
        for seg,p in zip(trans,prompts):
            t=int(seg["inicio"])
            w.writerow([t,f"{t} - {p}","PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])
    with open(proj_dir/f"{slug}.srt","w",encoding="utf-8") as f:
        for i,seg in enumerate(trans,1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")
    with open(proj_dir/f"{slug}.txt","w",encoding="utf-8") as f:
        f.write(descricao.strip())

    # uploads
    for ext in ("csv","srt","txt","mp3"):
        path=proj_dir/f"{slug}.{ext}"
        if path.exists():
            upload_drive(path, path.name, folderId, drive)

    return jsonify(folder_url=f"https://drive.google.com/drive/folders/{folderId}")

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    slug = request.form.get("slug")
    if not slug: return jsonify(error="slug obrigatório"),400
    proj_dir = Path(slug)
    proj_dir.mkdir(exist_ok=True)
    z = request.files.get("zip")
    if not z: return jsonify(error="ZIP obrigatório"),400
    tmp = proj_dir/"_raw"
    tmp.mkdir(exist_ok=True)
    (tmp/z.filename).write_bytes(z.read())
    with zipfile.ZipFile(tmp/z.filename) as zf:
        zf.extractall(tmp)
    imgs = list(tmp.glob("*.[jp][pn]g"))
    if not imgs: return jsonify(error="sem imagens"),400

    # lê prompts do CSV
    prompts=[]
    with open(proj_dir/f"{slug}.csv",encoding="utf-8") as f:
        for r in csv.DictReader(f):
            prompts.append(r["PROMPT"].split(" - ",1)[-1])

    sel=[]
    for i,_ in enumerate(prompts):
        dst=proj_dir/f"{i:02d}_{imgs[0].name}"
        imgs[0].rename(dst)
        sel.append(dst.name)
    return jsonify(usadas=sel)

@app.route("/montar_video", methods=["POST"])
def montar_video():
    data    = request.get_json() or {}
    slug    = data.get("slug")
    if not slug: return jsonify(error="slug obrigatório"),400
    proj_dir = Path(slug)

    # imagens e áudio
    imgs = sorted(proj_dir.glob("*.[jp][pn]g"))
    mp3s = list(proj_dir.glob("*.mp3"))
    if not imgs or not mp3s:
        return jsonify(error="arquivos faltando"),400
    audio = AudioFileClip(str(mp3s[0]))

    # lê SRT
    segs=[]
    with open(proj_dir/f"{slug}.srt",encoding="utf-8") as f:
        for blk in f.read().split("\n\n"):
            lines=blk.split("\n")
            if len(lines)>=3:
                st,en=lines[1].split(" --> ")
                txt=lines[2]
                def p(ts):
                    h,m,rest=ts.split(":"); s,ms=rest.split(",")
                    return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
                segs.append({"inicio":p(st),"fim":p(en),"texto":txt})

    clips=[]
    for idx,seg in enumerate(segs):
        dur=seg["fim"]-seg["inicio"]
        img=ImageClip(str(imgs[idx%len(imgs)])).resize(height=720)\
             .crop(x_center="center",width=1280).set_duration(dur)
        zoom=img.resize(lambda t:1+0.02*t)
        txt=TextClip(seg["texto"],fontsize=60,method="caption",
                     color="white",stroke_color="black",stroke_width=2)\
             .set_duration(dur).set_position(("center","bottom"))
        grain=make_grain().set_opacity(0.05).set_duration(dur)
        clips.append(CompositeVideoClip([zoom,grain,txt],size=(1280,720)))

    main_vid=concatenate_videoclips(clips).set_audio(audio)

    # efeito geral
    overlay = (VideoFileClip("sobrepor.mp4")
               .subclip(0,main_vid.duration)
               .resize((1280,720))
               .set_opacity(0.2)
               .set_fps(24))
    vid = CompositeVideoClip([main_vid,overlay],size=(1280,720))

    # watermark
    wm = (ImageClip("sobrepor.png")
          .resize(height=100)
          .set_position(("right","bottom"))
          .set_duration(vid.duration))
    vid = CompositeVideoClip([vid,wm],size=(1280,720)).set_audio(audio)

    # fechamento
    end_img = ImageClip("fechamento.png")\
              .resize((1280,720)).set_duration(3)
    end_eff = (VideoFileClip("sobrepor.mp4")
               .subclip(0,3)
               .resize((1280,720))
               .set_opacity(0.2)
               .set_fps(24))
    end_clip = CompositeVideoClip([end_img,end_eff],size=(1280,720))
    final   = concatenate_videoclips([vid,end_clip]).set_audio(audio)

    outp = proj_dir/f"{slug}.mp4"
    final.write_videofile(str(outp),fps=24,codec="libx264",audio_codec="aac")

    drive = get_drive_service()
    upload_drive(outp, outp.name, criar_pasta_drive(slug, drive), drive)
    return jsonify(video=f"project/{slug}/{slug}.mp4")

if __name__=="__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
