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

# pastas locais
BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR = BASE / "csv"
PROJECT_DIR = BASE / "project"
for d in (AUDIO_DIR, CSV_DIR, PROJECT_DIR):
    d.mkdir(exist_ok=True)

# configurações
GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
openai.api_key = os.getenv("OPENAI_API_KEY")

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

def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280,720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity,128+intensity,(size[1],size[0],1),dtype=np.uint8)
        return np.repeat(noise,3,axis=2)
    return VideoClip(frame, duration=1).set_fps(24)

@app.route("/")
def home():
    return "API OK"

@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json(force=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"),400

    slug = slugify(texto)
    mp3_path = AUDIO_DIR / f"{slug}.mp3"

    # ElevenLabs TTS
    def send_tts(payload):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN/stream"
        headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
        resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
        resp.raise_for_status()
        return resp.content

    payload = {"text": texto, "voice_settings": {"stability":0.6,"similarity_boost":0.9,"style":0.2}}
    try:
        audio = send_tts(payload)
    except:
        payload["voice_settings"].pop("style",None)
        audio = send_tts(payload)

    with open(mp3_path,"wb") as f:
        f.write(audio)

    return jsonify(slug=slug),200

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True) or {}
    slug = data.get("slug")
    if slug:
        candidates = [AUDIO_DIR/ f"{slug}.mp3"]
    else:
        candidates = list(AUDIO_DIR.glob("*.mp3"))
    if not candidates:
        return jsonify(error="MP3 não encontrado"),400

    audio_file = open(candidates[0],"rb")
    # gera SRT com Whisper
    srt = openai.audio.transcriptions.create(
        model="whisper-1", file=audio_file, response_format="srt"
    )
    audio_file.close()

    # parse SRT
    def parse_ts(ts):
        h,m,rest = ts.split(":"); s,ms = rest.split(",")
        return int(h)*3600+int(m)*60+int(s)+int(ms)/1000

    segments=[]
    for block in srt.strip().split("\n\n"):
        lines = block.split("\n")
        if len(lines)>=3:
            start,end = lines[1].split(" --> ")
            text = " ".join(lines[2:])
            segments.append({
                "inicio": parse_ts(start),
                "fim":    parse_ts(end),
                "texto":  text
            })
    # salva SRT
    srt_path = PROJECT_DIR / f"{slug}.srt"
    with open(srt_path,"w",encoding="utf-8") as f:
        f.write(srt)

    return jsonify(transcricao=segments),200

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json(force=True) or {}
    slug     = data.get("slug")
    trans    = data.get("transcricao",[])
    prompts  = data.get("prompts",[])
    descr    = data.get("descricao","")

    if not slug or not trans or len(trans)!=len(prompts):
        return jsonify(error="dados inválidos"),400

    # CSV
    csv_path = PROJECT_DIR / f"{slug}.csv"
    header=["PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL",
            "SEED_NUMBER","RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"]
    neg = "low quality,overexposed,underexposed,extra limbs,bad anatomy"
    with open(csv_path,"w",newline="",encoding="utf-8") as f:
        writer=csv.writer(f); writer.writerow(header)
        for seg,p in zip(trans,prompts):
            t0 = int(round(seg["inicio"]))
            prom = f"{t0} - Vibrant watercolor: {p}"
            writer.writerow([prom,"PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # TXT
    txt_path = PROJECT_DIR / f"{slug}.txt"
    with open(txt_path,"w",encoding="utf-8") as f:
        f.write(descr)

    return jsonify(ok=True),200

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    z = request.files.get("zip")
    if not z:
        return jsonify(error="campo 'zip' obrigatório"),400

    # slug único na pasta
    slugs = [p.stem for p in PROJECT_DIR.glob("*.srt")]
    if not slugs:
        return jsonify(error="nenhum projeto iniciado"),400
    slug=slugs[-1]
    # extrai diretamente em project/
    zfile = PROJECT_DIR / f"{slug}.zip"
    z.save(zfile)
    with zipfile.ZipFile(zfile,"r") as zf:
        zf.extractall(PROJECT_DIR)
    return jsonify(ok=True,slug=slug),200

@app.route("/montar_video", methods=["POST"])
def montar_video():
    data = request.get_json(force=True) or {}
    slug     = data.get("slug")
    folder_id= data.get("folder_id")
    if not slug or not folder_id:
        return jsonify(error="slug/folder_id obrigatórios"),400

    # busca WAVs, SRT, CSV e imagens
    audio = PROJECT_DIR / f"{slug}.mp3"
    srt   = PROJECT_DIR / f"{slug}.srt"
    csv_  = PROJECT_DIR / f"{slug}.csv"
    imgs  = sorted(PROJECT_DIR.glob("*.png")+PROJECT_DIR.glob("*.jpg"))

    if not audio.exists() or not srt.exists() or not csv_.exists() or not imgs:
        return jsonify(error="arquivos faltando"),400

    # lê transcrição do SRT
    def parse_ts(ts):
        h,m,rest = ts.split(":"); s,ms=rest.split(",")
        return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
    blocks=[]
    for blk in srt.read_text(encoding="utf-8").strip().split("\n\n"):
        ln=blk.split("\n")
        if len(ln)>=3:
            start,end=ln[1].split(" --> ")
            txt=" ".join(ln[2:])
            blocks.append({
                "inicio":parse_ts(start),
                "fim":   parse_ts(end),
                "texto": txt
            })

    audio_clip = AudioFileClip(str(audio))
    clips=[]
    for i,blk in enumerate(blocks):
        dur = blk["fim"]-blk["inicio"]
        img = ImageClip(str(imgs[i%len(imgs)]))\
              .resize(height=720).crop(width=1280,x_center="center")\
              .set_duration(dur)
        txt = TextClip(blk["texto"].upper(), fontsize=60, font="DejaVu-Sans-Bold",
                       stroke_color="black", stroke_width=2, size=(1280,None),
                       method="caption").set_duration(dur).set_position(("center","bottom"))
        grain = make_grain().set_opacity(0.05).set_duration(dur)
        comp  = CompositeVideoClip([img,grain,txt], size=(1280,720))
        clips.append(comp)

    final = concatenate_videoclips(clips).set_audio(audio_clip)
    out   = PROJECT_DIR / f"{slug}.mp4"
    final.write_videofile(str(out), fps=24, codec="libx264", audio_codec="aac")

    # upload tudo ao Drive
    drive = get_drive_service()
    folder = drive.files().create(
        body={"name":slug,"mimeType":"application/vnd.google-apps.folder",
              "parents":[GOOGLE_DRIVE_FOLDER_ID]},
        fields="id").execute().get("id")
    for f in PROJECT_DIR.iterdir():
        MediaFileUpload(str(f), resumable=True)
        drive.files().create(
            body={"name":f.name,"parents":[folder]},
            media_body=MediaFileUpload(str(f), resumable=True),
            fields="id").execute()

    return jsonify(link=f"https://drive.google.com/drive/folders/{folder}"),200

# servir estáticos
@app.route("/project/<path:fn>")
def baixar(fn):
    return send_from_directory(PROJECT_DIR, fn)

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
