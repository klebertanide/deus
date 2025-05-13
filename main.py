# Bloco 1 – Imports e Configurações Iniciais
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
    d.mkdir(parents=True, exist_ok=True)

# Configs de API
GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY        = os.getenv("ELEVENLABS_API_KEY")
openai.api_key        = os.getenv("OPENAI_API_KEY")


# Bloco 2 – Utilitários de Drive e Texto
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(texto, limite=30):
    texto = unidecode.unidecode(texto)
    texto = re.sub(r"[^\w\s]", "", texto)
    return texto.strip().replace(" ", "_")[:limite].lower()

def criar_pasta_drive(slug, drive):
    metadata = {
        "name": slug,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [GOOGLE_DRIVE_FOLDER_ID]
    }
    pasta = drive.files().create(body=metadata, fields="id").execute()
    return pasta["id"]

def upload_arquivo_drive(path, name, folder_id, drive):
    media = MediaFileUpload(str(path), resumable=True)
    file_metadata = {"name": name, "parents": [folder_id]}
    drive.files().create(body=file_metadata, media_body=media, fields="id").execute()


# Bloco 3 – Grão de vídeo e timestamps
def format_ts(seconds: float) -> str:
    ms = int((seconds % 1) * 1000)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280, 720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity, 128+intensity, (size[1], size[0], 1), np.uint8)
        return np.repeat(noise, 3, axis=2)
    return VideoClip(frame, duration=1).set_fps(24)


# Bloco 4 – Endpoints estáticos
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

@app.route("/.well-known/openapi.json")
def serve_openapi():
    return send_from_directory(".well-known", "openapi.json", mimetype="application/json")

@app.route("/.well-known/ai-plugin.json")
def serve_plugin():
    return send_from_directory(".well-known", "ai-plugin.json", mimetype="application/json")


# Bloco 5 – /falar
@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify({"error": "campo 'texto' obrigatório"}), 400

    # slug do versículo/frase
    slug     = slugify(texto)
    filename = f"{slug}.mp3"
    out_path = AUDIO_DIR / filename

    # Chama ElevenLabs
    payload1 = {"text": texto, "voice_settings": {"stability":0.6,"similarity_boost":0.9,"style":0.2}}
    headers  = {"xi-api-key": ELEVEN_API_KEY}
    resp = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN/stream",
        headers={**headers,"Content-Type":"application/json"},
        json=payload1, stream=True, timeout=60
    )
    if not resp.ok:
        return jsonify({"error":"falha ElevenLabs","detalhe":resp.text}), 500

    with open(out_path, "wb") as f:
        f.write(resp.content)

    return jsonify({
        "audio_url": request.url_root.rstrip("/") + f"/audio/{filename}",
        "filename": filename,
        "slug": slug
    })


# Bloco 6 – /transcrever
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error":"campo 'audio_url' obrigatório"}), 400

    # carrega local ou remoto
    if audio_url.startswith(request.url_root.rstrip("/")):
        fname      = audio_url.rsplit("/audio/",1)[-1]
        audio_file = open(AUDIO_DIR / fname, "rb")
    else:
        r = requests.get(audio_url, timeout=60)
        r.raise_for_status()
        audio_file = io.BytesIO(r.content)
        audio_file.name = "audio.mp3"

    try:
        # chamada certa para Whisper
        transcript = openai.Audio.transcribe(
            "whisper-1",
            file=audio_file,
            response_format="verbose_json"
        )
        segments = [
            {"inicio": s["start"], "fim": s["end"], "texto": s["text"]}
            for s in transcript["segments"]
        ]
        total_dur = segments[-1]["fim"] if segments else 0.0
        return jsonify({"duracao_total": total_dur, "transcricao": segments})

    except Exception as e:
        return jsonify({"error":str(e)}), 500

    finally:
        audio_file.close()


# Bloco 7 – /gerar_csv
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data         = request.get_json() or {}
    transcricao  = data.get("transcricao", [])
    prompts      = data.get("prompts", [])
    descricao    = data.get("descricao", "")
    mp3_filename = data.get("mp3_filename")  # opcional

    # detecta único MP3 se não vier
    if not mp3_filename:
        mp3s = list(AUDIO_DIR.glob("*.mp3"))
        if len(mp3s)==1:
            mp3_filename = mp3s[0].name
        elif not mp3s:
            return jsonify({"error":"Nenhum .mp3 na pasta"}),400
        else:
            return jsonify({"error":"Múltiplos .mp3 encontrados; informe 'mp3_filename'"}),400

    slug     = Path(mp3_filename).stem
    mp3_path = AUDIO_DIR/mp3_filename

    if not transcricao or not prompts or len(transcricao)!=len(prompts):
        return jsonify({"error":"transcricao+prompts inválidos"}),400
    if not mp3_path.exists():
        return jsonify({"error":"MP3 não encontrado"}),400

    drive    = get_drive_service()
    pasta_id = criar_pasta_drive(slug, drive)

    csv_path = CSV_DIR/f"{slug}.csv"
    srt_path = FILES_DIR/f"{slug}.srt"
    txt_path = FILES_DIR/f"{slug}.txt"

    # CSV de prompts
    neg = "low quality, overexposed, underexposed, extra limbs, extra fingers, missing fingers, disfigured, deformed, bad anatomy"
    header = ["PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL","SEED_NUMBER","RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"]
    with open(csv_path,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(header)
        for seg,p in zip(transcricao,prompts):
            sec = int(round(seg["inicio"]))
            prompt_en = f"{sec} - Vibrant watercolor style, modern digital art. {p}"
            w.writerow([prompt_en,"PRIVATE","9:16","ON","3.0","", "TURBO", neg,"AUTO",""])

    # SRT
    with open(srt_path,"w",encoding="utf-8") as s:
        for i,seg in enumerate(transcricao,1):
            s.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto'].strip()}\n\n")

    # TXT
    with open(txt_path,"w",encoding="utf-8") as t:
        t.write(descricao.strip())

    # Upload
    upload_arquivo_drive(csv_path, f"{slug}.csv", pasta_id, drive)
    upload_arquivo_drive(srt_path, f"{slug}.srt", pasta_id, drive)
    upload_arquivo_drive(txt_path, f"{slug}.txt", pasta_id, drive)
    upload_arquivo_drive(mp3_path, f"{slug}.mp3", pasta_id, drive)

    return jsonify({"folder_url":f"https://drive.google.com/drive/folders/{pasta_id}"})


# Bloco 8 – /upload_zip
@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify({"error":"Campo 'zip' obrigatório."}),400

    projetos = [p for p in FILES_DIR.iterdir() if p.is_dir() and not p.name.endswith("_raw")]
    if len(projetos)!=1:
        return jsonify({"error":"Deve existir exatamente UMA pasta de projeto."}),400

    slug       = projetos[0].name
    temp_dir   = FILES_DIR/f"{slug}_raw"
    output_dir = FILES_DIR/slug
    temp_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    zip_path = temp_dir/"imagens.zip"
    file.save(zip_path)
    with zipfile.ZipFile(zip_path,"r") as z: z.extractall(temp_dir)

    imgs = [f for f in temp_dir.glob("*.*") if f.suffix.lower() in [".jpg",".jpeg",".png"]]
    if not imgs:
        return jsonify({"error":"Nenhuma imagem no ZIP."}),400

    csv_path = CSV_DIR/f"{slug}.csv"
    if not csv_path.exists():
        return jsonify({"error":"CSV do projeto não encontrado."}),400

    prompts=[]
    with open(csv_path,newline="",encoding="utf-8") as f:
        for row in csv.DictReader(f):
            prompts.append(row["PROMPT"].split(" - ",1)[-1].strip())

    usadas=[]
    from sentence_transformers import SentenceTransformer, util
    model = SentenceTransformer("clip-ViT-B-32")
    for idx,p in enumerate(prompts):
        pe = model.encode(p, convert_to_tensor=True)
        best,score=None,-1
        for img in imgs:
            name_clean = re.sub(r"[^\w\s]"," ",img.stem)
            ie = model.encode(name_clean, convert_to_tensor=True)
            sc = util.cos_sim(pe,ie).item()
            if sc>score: best,score=img,sc
        dst = output_dir/f"{idx:02d}_{best.name}"
        best.rename(dst)
        imgs.remove(best)
        usadas.append(dst.name)

    return jsonify({"ok":True,"slug":slug,"usadas":usadas})


# Bloco 9 – /montar_video
@app.route("/montar_video", methods=["POST"])
def montar_video():
    data      = request.get_json(force=True) or {}
    slug      = data.get("slug")
    folder_id = data.get("folder_id")
    if not slug or not folder_id:
        return jsonify({"error":"slug e folder_id obrigatórios"}),400

    # coleta assets
    audio_list = list(AUDIO_DIR.glob("*.mp3"))
    mp3_path   = audio_list[0] if audio_list else None
    srt_list   = list(FILES_DIR.glob("*.srt"))
    srt_path   = srt_list[0] if srt_list else None
    csv_list   = list(CSV_DIR.glob("*.csv"))
    csv_path   = csv_list[0] if csv_list else None
    img_dir    = FILES_DIR/slug
    images     = sorted([f for f in img_dir.iterdir() if f.suffix.lower() in [".jpg",".png",".jpeg"]])

    if not all([mp3_path,srt_path,csv_path,images]):
        return jsonify({"error":"Recursos insuficientes para montar vídeo"}),400

    # lê transcrição
    trans=[]
    with open(srt_path,encoding="utf-8") as f:
        for block in f.read().strip().split("\n\n"):
            lines=block.split("\n")
            if len(lines)>=3:
                st,et = lines[1].split(" --> ")
                def pt(ts): h,m,rest=ts.split(":"); s,ms=rest.split(","); return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
                trans.append({"inicio":pt(st),"fim":pt(et),"texto":" ".join(lines[2:])})

    audio_clip = AudioFileClip(str(mp3_path))
    clips=[]
    from difflib import SequenceMatcher
    def sim(a,b): return SequenceMatcher(None,a.lower(),b.lower()).ratio()

    for i,seg in enumerate(trans):
        dur   = seg["fim"]-seg["inicio"]
        img   = images[min(i,len(images)-1)]
        bg    = ImageClip(str(img)).resize(height=720).crop(x_center="center",width=1280).set_duration(dur)
        zoom  = bg.resize(lambda t:1+0.02*t)
        txt   = TextClip(seg["texto"].upper(), fontsize=60, font="DejaVu-Sans-Bold",
                         color="white", stroke_color="black", stroke_width=2,
                         size=(1280,None), method="caption").set_duration(dur).set_position(("center","bottom"))
        grain = make_grain().set_opacity(0.05).set_duration(dur)
        comp  = CompositeVideoClip([zoom,grain,txt], size=(1280,720))
        clips.append(comp)

    final = concatenate_videoclips(clips).set_audio(audio_clip)
    outp  = FILES_DIR/f"{slug}.mp4"
    final.write_videofile(str(outp),fps=24,codec="libx264",audio_codec="aac")

    drive = get_drive_service()
    upload_arquivo_drive(outp,"video_final.mp4",folder_id,drive)
    return jsonify({"video":f"https://drive.google.com/drive/folders/{folder_id}"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)