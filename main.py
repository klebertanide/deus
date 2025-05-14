import os
import io
import csv
import re
import zipfile
import uuid
import requests
import unidecode
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import openai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload, MediaIoBaseUpload
from moviepy.editor import (
    AudioFileClip, ImageClip, TextClip,
    CompositeVideoClip, concatenate_videoclips
)
from moviepy.video.VideoClip import VideoClip
import numpy as np

app = Flask(__name__)

# ————————— CONFIGURAÇÕES GERAIS —————————
BASE       = Path(".")
AUDIO_DIR  = BASE / "audio"
CSV_DIR    = BASE / "csv"
FILES_DIR  = BASE / "downloads"
VIDEOS_DIR = BASE / "videos"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR, VIDEOS_DIR):
    d.mkdir(exist_ok=True, parents=True)

GOOGLE_DRIVE_PARENT = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
SERVICE_SECRET      = "/etc/secrets/service_account.json"
openai.api_key      = os.getenv("OPENAI_API_KEY")
ELEVEN_API_KEY      = os.getenv("ELEVENLABS_API_KEY")

# Globals para manter contexto entre chamadas
LAST_SLUG      = None
LAST_FOLDER_ID = None

# ————————— HELPERS —————————
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_SECRET,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def upload_arquivo_drive(path_or_buf, name, folder_id, drive=None):
    drv = drive or get_drive_service()
    meta = {'name': name, 'parents': [folder_id]}
    if isinstance(path_or_buf, io.BytesIO):
        media = MediaIoBaseUpload(path_or_buf, mimetype='video/mp4' if name.endswith('.mp4') else None)
    else:
        media = MediaFileUpload(str(path_or_buf), resumable=True)
    drv.files().create(body=meta, media_body=media).execute()

def slugify(text, limit=30):
    txt = unidecode.unidecode(text)
    txt = re.sub(r"[^\w\s]", "", txt)
    return txt.strip().replace(" ", "_").lower()[:limit]

def elevenlabs_tts(texto):
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": texto,
        "voice_settings": {
            "stability": 0.60,
            "similarity_boost": 0.90,
            "style": 0.15,
            "use_speaker_boost": True
        },
        "model_id": "eleven_multilingual_v2",
        "voice_id": "cwIsrQsWEVTols6slKYN"
    }
    r = requests.post("https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
                      headers=headers, json=payload)
    r.raise_for_status()
    return r.content

def parse_srt(srt_text):
    def to_sec(x):
        h,m,rest = x.split(":")
        s,ms     = rest.split(",")
        return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000
    out=[]
    for block in srt_text.strip().split("\n\n"):
        lines = block.split("\n")
        if len(lines)>=3:
            start,end = lines[1].split(" --> ")
            txt = " ".join(lines[2:])
            out.append((to_sec(start), to_sec(end), txt))
    return out

# ————————— PARTE 1 —————————

@app.route("/falar", methods=["POST"])
def falar():
    global LAST_SLUG
    data  = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug    = slugify(texto)
    LAST_SLUG = slug
    mp3_fn  = f"{slug}.mp3"
    outpath = AUDIO_DIR / mp3_fn

    try:
        audio_bytes = elevenlabs_tts(texto)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    with open(outpath, "wb") as f:
        f.write(audio_bytes)

    return jsonify(
        audio_url = request.url_root.rstrip("/") + f"/audio/{mp3_fn}",
        slug      = slug
    )

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    # carrega o mp3
    if audio_url.startswith(request.url_root.rstrip("/")):
        fname = audio_url.rsplit("/audio/",1)[-1]
        file  = open(AUDIO_DIR / fname, "rb")
    else:
        resp = requests.get(audio_url, timeout=60); resp.raise_for_status()
        file = io.BytesIO(resp.content); file.name = audio_url.split("/")[-1]

    try:
        srt = openai.audio.transcriptions.create(
            model="whisper-1", file=file, response_format="srt"
        )
        segments = parse_srt(srt)
        return jsonify(duracao_total=segments[-1][1], transcricao=[
            {"inicio":st, "fim":en, "texto":txt} for st,en,txt in segments
        ])
    except Exception as e:
        return jsonify(error=str(e)), 500
    finally:
        try: file.close()
        except: pass

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    global LAST_FOLDER_ID, LAST_SLUG
    data           = request.get_json() or {}
    transcricao    = data.get("transcricao", [])
    prompts        = data.get("prompts", [])
    descricao      = data.get("descricao", "")
    texto_original = data.get("texto_original", "")

    if not transcricao or not prompts or len(transcricao)!=len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    slug = slugify(texto_original or descricao)
    LAST_SLUG = slug

    drive     = get_drive_service()
    pasta_meta= {
        'name': slug,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [GOOGLE_DRIVE_PARENT]
    }
    res       = drive.files().create(body=pasta_meta, fields='id').execute()
    folder_id = res['id']
    LAST_FOLDER_ID = folder_id

    # CSV, SRT, TXT e MP3
    csv_path = CSV_DIR / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"
    mp3_path = AUDIO_DIR / f"{slug}.mp3"

    #  — CSV —
    neg = "low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w=csv.writer(f)
        w.writerow(["TIME","PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL","SEED","RENDERING","NEGATIVE","STYLE","PALETTE"])
        for seg,p in zip(transcricao,prompts):
            t=int(seg["inicio"])
            w.writerow([t,f"{t} - {p}","PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    #  — SRT —
    def fmt(s):
        ms=int((s%1)*1000); h=int(s//3600); m=int((s%3600)//60); sec=int(s%60)
        return f"{h:02}:{m:02}:{sec:02},{ms:03}"
    with open(srt_path,"w",encoding="utf-8") as f:
        for i,(st,en,txt) in enumerate(parse_srt("\n\n".join([
            f"{i+1}\n{fmt(st)} --> {fmt(en)}\n{txt}\n"
            for i,(st,en,txt) in enumerate(parse_srt("\n\n".join([])))
        ])),1):
            f.write(f"{i}\n{fmt(st)} --> {fmt(en)}\n{txt}\n\n")

    #  — TXT —
    with open(txt_path,"w",encoding="utf-8") as f:
        f.write(descricao.strip())

    # upload de todos
    for p,path in [("csv",csv_path),("srt",srt_path),("txt",txt_path),("mp3",mp3_path)]:
        if path.exists():
            upload_arquivo_drive(path, f"{slug}.{p}", folder_id, drive)

    return jsonify(
        slug=slug,
        folder_id=folder_id,
        folder_url=f"https://drive.google.com/drive/folders/{folder_id}"
    )

# ————————— Mensagem de transição —————————
@app.route("/próxima_etapa", methods=["GET"])
def proxima_etapa():
    return jsonify(
        mensagem=(
            "Ótimo! Já carreguei seu áudio, legenda e prompts na pasta do Drive.\n"
            "Agora, para continuar e montar o vídeo, envie o **ZIP** gerado pelo Ideogram "
            "(com todas as imagens) para o endpoint `/upload_zip`. "
            "Ele vai extrair e selecionar as melhores imagens para cada trecho."
        )
    )

# ————————— PARTE 2 —————————

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    global LAST_SLUG, LAST_FOLDER_ID
    slug      = request.form.get("slug")     or LAST_SLUG
    folder_id = request.form.get("folder_id")or LAST_FOLDER_ID
    file      = request.files.get("zip")
    if not slug or not folder_id or not file:
        return jsonify(error="slug, folder_id e zip são obrigatórios"), 400

    LAST_SLUG, LAST_FOLDER_ID = slug, folder_id

    # extrai e seleciona em memória (placeholder: todas)
    buf = io.BytesIO(file.read())
    sel = []
    with zipfile.ZipFile(buf) as z:
        for n in z.namelist():
            if n.lower().endswith((".jpg",".jpeg",".png")):
                sel.append((n, z.read(n)))

    drive    = get_drive_service()
    uploaded = []
    for name,data in sel:
        bio = io.BytesIO(data); bio.seek(0)
        media = MediaIoBaseUpload(bio, mimetype="image/jpeg")
        drive.files().create(
            body={"name": name,"parents":[folder_id]},
            media_body=media
        ).execute()
        uploaded.append(name)

    return jsonify(slug=slug, folder_id=folder_id, images=uploaded)

@app.route("/montar_video", methods=["POST"])
def montar_video():
    global LAST_SLUG, LAST_FOLDER_ID
    data      = request.get_json() or {}
    slug      = data.get("slug")      or LAST_SLUG
    folder_id = data.get("folder_id") or LAST_FOLDER_ID
    if not slug or not folder_id:
        return jsonify(error="slug e folder_id são obrigatórios"), 400

    LAST_SLUG, LAST_FOLDER_ID = slug, folder_id
    drive = get_drive_service()

    # listar e baixar mp3, srt e imagens do Drive
    page, files = None, []
    q = f"'{folder_id}' in parents and trashed=false"
    while True:
        resp = drive.files().list(q=q, fields="nextPageToken,files(id,name)", pageToken=page).execute()
        files += resp["files"]; page = resp.get("nextPageToken")
        if not page: break

    mp3_data = None; srt_txt = None; imgs_data = []
    for f in files:
        name, fid = f["name"], f["id"]
        buf = io.BytesIO()
        dl = MediaIoBaseDownload(buf, drive.files().get_media(fileId=fid))
        done = False
        while not done:
            _, done = dl.next_chunk()
        buf.seek(0)
        if name.lower().endswith(".mp3"):
            mp3_data = buf.read()
        elif name.lower().endswith(".srt"):
            srt_txt = buf.read().decode("utf-8")
        elif name.lower().endswith((".jpg",".jpeg",".png")):
            imgs_data.append(buf.read())

    if not mp3_data or not srt_txt or not imgs_data:
        return jsonify(error="mp3, srt ou imagens ausentes"), 400

    # montar vídeo
    audio = AudioFileClip(io.BytesIO(mp3_data))
    segments = parse_srt(srt_txt)
    clips = []
    for idx,(st,end,txt) in enumerate(segments):
        dur = end - st
        # imagem com zoom e crop
        img0 = (ImageClip(io.BytesIO(imgs_data[idx % len(imgs_data)]))
                .resize(height=720)
                .crop(x_center="center", width=1280)
                .set_duration(dur))
        zoomed = img0.resize(lambda t: 1 + 0.02*t)
        # grão
        def grain_frame(t):
            noise = np.random.randint(118,138,(720,1280,1),np.uint8)
            return np.repeat(noise,3,axis=2)
        grain = VideoClip(grain_frame, duration=dur).set_fps(24).set_opacity(0.05)
        # legenda estilizada
        txt_clip = (TextClip(txt, fontsize=50, font="Arial-Bold",
                            color="white", stroke_color="black",
                            stroke_width=2, method="caption", size=(1200,None))
                    .set_duration(dur).set_position(("center","bottom")))
        comp = CompositeVideoClip([zoomed, grain, txt_clip], size=(1280,720))\
               .set_audio(audio.subclip(st,end))
        clips.append(comp)

    final = concatenate_videoclips(clips)
    out_buf = io.BytesIO()
    final.write_videofile(out_buf, fps=24, codec="libx264", audio_codec="aac")
    out_buf.seek(0)

    # envia MP4 ao Drive
    upload_arquivo_drive(out_buf, f"{slug}.mp4", folder_id, drive)
    return jsonify(video_url=f"https://drive.google.com/drive/folders/{folder_id}")

# ————————— Rotas estáticas —————————
@app.route("/audio/<path:fn>")
def serve_audio(fn):
    return send_from_directory(AUDIO_DIR, fn)

@app.route("/csv/<path:fn>")
def serve_csv(fn):
    return send_from_directory(CSV_DIR, fn)

@app.route("/downloads/<path:fn>")
def serve_download(fn):
    return send_from_directory(FILES_DIR, fn)

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)
