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
from sentence_transformers import SentenceTransformer, util

app = Flask(__name__)

# Directories
BASE        = Path(".")
AUDIO_DIR   = BASE / "audio"
CSV_DIR     = BASE / "csv"
FILES_DIR   = BASE / "downloads"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Credentials & API keys
GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY        = os.getenv("ELEVENLABS_API_KEY")
openai.api_key         = os.getenv("OPENAI_API_KEY")

# CLIP model for image selection
clip_model = SentenceTransformer("clip-ViT-B-32")

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(text, limit=30):
    txt = unidecode.unidecode(text)
    txt = re.sub(r"[^\w\s]", "", txt).strip().replace(" ", "_")
    return txt[:limit].lower()

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

def selecionar_imagem_mais_similar(prompt, imagens):
    emb_p = clip_model.encode(prompt, convert_to_tensor=True)
    best_score, best_img = -1, None
    for img in imagens:
        name = re.sub(r"[^\w\s]", " ", img.stem)
        emb_i = clip_model.encode(name, convert_to_tensor=True)
        score = util.cos_sim(emb_p, emb_i).item()
        if score > best_score:
            best_score, best_img = score, img
    return best_img

def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN", retries=3):
    def attempt(payload):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
        for i in range(retries):
            resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
            if resp.ok:
                return resp.content
            time.sleep(2**i)
        resp.raise_for_status()
    p1 = {"text": text, "voice_settings":{"stability":0.6,"similarity_boost":0.9,"style":0.2}}
    try:
        audio = attempt(p1)
        if not audio: raise ValueError
        return audio
    except:
        p2 = {"text": text, "voice_settings":{"stability":0.6,"similarity_boost":0.9}}
        audio = attempt(p2)
        if not audio: raise ValueError
        return audio

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
def serve_files(fn):
    return send_from_directory(FILES_DIR, fn)

# 1. Generate TTS
@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400
    slug     = slugify(texto)
    filename = f"{slug}.mp3"
    path     = AUDIO_DIR / filename
    try:
        audio = elevenlabs_tts(texto)
        with open(path, "wb") as f: f.write(audio)
    except Exception as e:
        return jsonify(error="falha TTS", detalhe=str(e)), 500
    return jsonify(audio_url=request.url_root.rstrip("/")+f"/audio/{filename}",
                   filename=filename, slug=slug)

# 2. Transcribe with Whisper → SRT + segments
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"), 400
    try:
        if audio_url.startswith(request.url_root.rstrip("/")):
            fname      = audio_url.rsplit("/audio/",1)[-1]
            audio_file = open(AUDIO_DIR/fname,"rb")
        else:
            r = requests.get(audio_url,timeout=60); r.raise_for_status()
            audio_file = io.BytesIO(r.content); audio_file.name="audio.mp3"
        srt_text = openai.audio.transcriptions.create(
            model="whisper-1", file=audio_file, response_format="srt"
        )
        def parse_ts(ts):
            h,m,rest = ts.split(":"); s,ms = rest.split(",")
            return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
        segments=[]
        for block in srt_text.strip().split("\n\n"):
            lines = block.split("\n")
            if len(lines)>=3:
                start,end = lines[1].split(" --> ")
                text      = " ".join(lines[2:])
                segments.append({
                    "inicio": parse_ts(start),
                    "fim":    parse_ts(end),
                    "texto":  text
                })
        return jsonify(duracao_total=segments[-1]["fim"] if segments else 0,
                       transcricao=segments)
    except Exception as e:
        return jsonify(error=str(e)), 500
    finally:
        try: audio_file.close()
        except: pass

# 3. Generate CSV, SRT, TXT & upload to Drive
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data        = request.get_json() or {}
    transcricao = data.get("transcricao",[])
    prompts     = data.get("prompts",[])
    descricao   = data.get("descricao","")
    mp3_fname   = data.get("mp3_filename")
    # auto-detect mp3 if missing
    if not mp3_fname:
        mp3s = list(AUDIO_DIR.glob("*.mp3"))
        if len(mp3s)==1:
            mp3_fname = mp3s[0].name
        else:
            return jsonify(error="informe 'mp3_filename' ou garanta um único .mp3"),400
    slug = data.get("slug", Path(mp3_fname).stem)
    if len(transcricao)!=len(prompts) or not transcricao:
        return jsonify(error="transcricao+prompts inválidos"),400
    mp3_path = AUDIO_DIR/mp3_fname
    if not mp3_path.exists():
        return jsonify(error="MP3 não encontrado"),400
    # prepare drive folder
    drive    = get_drive_service()
    folder   = drive.files().create(
        body={"name":slug,"mimeType":"application/vnd.google-apps.folder",
              "parents":[GOOGLE_DRIVE_FOLDER_ID]},fields="id"
    ).execute().get("id")
    # paths
    csv_path = CSV_DIR/f"{slug}.csv"
    srt_path = FILES_DIR/f"{slug}.srt"
    txt_path = FILES_DIR/f"{slug}.txt"
    # CSV
    neg = "low quality, overexposed, underexposed, extra limbs, disfigured"
    with open(csv_path,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        w.writerow(["PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL",
                    "SEED_NUMBER","RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"])
        for seg,p in zip(transcricao,prompts):
            sec = seg["inicio"]
            w.writerow([f"{sec:.1f} - Painting style: Traditional watercolor. {p}",
                        "PRIVATE","9:16","ON","3.0","", "TURBO", neg, "AUTO",""])
    # SRT
    with open(srt_path,"w",encoding="utf-8") as f:
        for i,seg in enumerate(transcricao,1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n"
                    f"{seg['texto'].strip()}\n\n")
    # TXT
    with open(txt_path,"w",encoding="utf-8") as f:
        f.write(descricao.strip())
    # Upload
    for p, name in ((csv_path,"prompts.csv"),(srt_path,"legenda.srt"),
                    (txt_path,"descricao.txt"),(mp3_path,"voz.mp3")):
        Media = MediaFileUpload(str(p),resumable=True)
        drive.files().create(body={"name":name,"parents":[folder]},
                             media_body=Media,fields="id").execute()
    return jsonify(folder_url=f"https://drive.google.com/drive/folders/{folder}")

# 4. Upload ZIP and extract images
@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify(error="Campo 'zip' obrigatório"),400
    # detect slug by latest CSV
    csvs = sorted(CSV_DIR.glob("*.csv"), key=os.path.getmtime, reverse=True)
    if not csvs:
        return jsonify(error="Nenhum CSV para inferir projeto"),400
    slug = csvs[0].stem
    out  = FILES_DIR/slug
    out.mkdir(exist_ok=True)
    tmp  = FILES_DIR/f"{slug}_tmp"
    tmp.mkdir(exist_ok=True)
    path = tmp/"imgs.zip"
    file.save(path)
    with zipfile.ZipFile(path,"r") as z:
        z.extractall(tmp)
    imgs = [f for f in tmp.glob("*") if f.suffix.lower() in [".jpg",".jpeg",".png"]]
    for img in imgs:
        dst = out/img.name
        img.rename(dst)
    # clean
    try: tmp.rmdir()
    except: pass
    return jsonify(ok=True, slug=slug, imagens=[i.name for i in out.iterdir()])

# 5. Assemble video
@app.route("/montar_video", methods=["POST"])
def montar_video():
    data     = request.get_json() or {}
    slug     = data.get("slug")
    folder_id= data.get("folder_id")
    if not slug or not folder_id:
        return jsonify(error="slug e folder_id obrigatórios"),400
    # locate resources
    imgs = sorted((FILES_DIR/slug).glob("*.[pj][pn]g"))
    if not imgs:
        return jsonify(error="Nenhuma imagem extraída"),400
    mp3s = list(AUDIO_DIR.glob("*.mp3"))
    if not mp3s:
        return jsonify(error="Nenhum áudio encontrado"),400
    audio_path = mp3s[0]
    # read csv prompts+times
    csvf = CSV_DIR/f"{slug}.csv"
    prompts=[]
    times=[]
    with open(csvf,newline="",encoding="utf-8") as f:
        r=csv.reader(f); next(r)
        for row in r:
            t,p = row[0].split(" - ",1)
            times.append(float(t)); prompts.append(p)
    # for each prompt pick best image
    assoc=[]
    used=set()
    for p in prompts:
        best=selecionar_imagem_mais_similar(p, imgs)
        assoc.append(best if best else imgs[0])
    # build video
    audio_clip = AudioFileClip(str(audio_path))
    clips=[]
    # read SRT for segments
    srtf = FILES_DIR/f"{slug}.srt"
    segs=[]
    with open(srtf,encoding="utf-8") as f:
        for block in f.read().strip().split("\n\n"):
            lines=block.split("\n")
            if len(lines)>=3:
                t0,t1 = lines[1].split(" --> ")
                def p2s(ts): h,m,rest=ts.split(":"); s,ms=rest.split(","); return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
                segs.append({
                    "inicio":p2s(t0),"fim":p2s(t1),"texto":" ".join(lines[2:])
                })
    for i,seg in enumerate(segs):
        dur = seg["fim"]-seg["inicio"]
        img = ImageClip(str(assoc[i])).resize(height=720).crop(width=1280, x_center=640).set_duration(dur)
        zoom=img.resize(lambda t:1+0.02*t)
        txt = TextClip(seg["texto"].upper(), fontsize=60, font="DejaVu-Sans-Bold",
                       stroke_color="black", stroke_width=2, size=(1280,None),
                       method="caption").set_duration(dur).set_position(("center","bottom"))
        grain = make_grain().set_opacity(0.05).set_duration(dur)
        comp = CompositeVideoClip([zoom,grain,txt], size=(1280,720))
        clips.append(comp)
    final = concatenate_videoclips(clips).set_audio(audio_clip)
    outp = FILES_DIR/f"{slug}.mp4"
    final.write_videofile(str(outp), fps=24, codec="libx264", audio_codec="aac")
    # upload to Drive
    media = MediaFileUpload(str(outp), resumable=True)
    drive = get_drive_service()
    drive.files().create(body={"name":"video.mp4","parents":[folder_id]},
                         media_body=media, fields="id").execute()
    return jsonify(ok=True, video_url=f"https://drive.google.com/drive/folders/{folder_id}")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)
