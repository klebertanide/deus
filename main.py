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

# Diretórios locais
BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR   = BASE / "csv"
FILES_DIR = BASE / "downloads"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Configurações de API
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


def criar_pasta_drive(slug, drive):
    meta = {
        "name": slug,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [GOOGLE_DRIVE_FOLDER_ID]
    }
    pasta = drive.files().create(body=meta, fields="id").execute()
    return pasta["id"]


def upload_arquivo_drive(filepath, filename, folder_id, drive):
    meta = {"name": filename, "parents": [folder_id]}
    media = MediaFileUpload(str(filepath), resumable=True)
    drive.files().create(body=meta, media_body=media, fields="id").execute()


def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN", retries=3):
    def enviar(payload):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
        for i in range(retries):
            r = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
            if r.ok:
                return r.content
            time.sleep(2 ** i)
        r.raise_for_status()

    # tentativa com style
    try:
        audio = enviar({
            "text": text,
            "voice_settings": {"stability": 0.6, "similarity_boost": 0.9, "style": 0.2}
        })
    except Exception:
        # fallback sem style
        audio = enviar({
            "text": text,
            "voice_settings": {"stability": 0.6, "similarity_boost": 0.9}
        })

    if not isinstance(audio, (bytes, bytearray)):
        raise RuntimeError("TTS retornou formato inesperado")
    return audio


def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"


def make_grain(size=(1280, 720), intensity=10):
    def frame(t):
        noise = np.random.randint(128 - intensity, 128 + intensity,
                                  (size[1], size[0], 1), dtype=np.uint8)
        noise = np.repeat(noise, 3, axis=2)
        return noise

    return VideoClip(frame, duration=1).set_fps(24)


@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"


# -- Endpoint /falar
@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug = slugify(texto)
    filename = f"{slug}.mp3"
    path = AUDIO_DIR / filename

    try:
        audio_bytes = elevenlabs_tts(texto)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    path.write_bytes(audio_bytes)
    return jsonify(
        audio_url=request.url_root.rstrip("/") + f"/audio/{filename}",
        filename=filename,
        slug=slug
    )


# -- Endpoint /transcrever
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    try:
        if audio_url.startswith(request.url_root.rstrip("/")):
            fname = audio_url.rsplit("/audio/", 1)[-1]
            audio_file = open(AUDIO_DIR / fname, "rb")
        else:
            r = requests.get(audio_url, timeout=60)
            r.raise_for_status()
            audio_file = io.BytesIO(r.content)
            audio_file.name = "audio.mp3"

        # gera SRT
        srt_text = openai.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="srt"
        )

        # parse timestamps
        def parse_ts(ts):
            h, m, rest = ts.split(":")
            s, ms = rest.split(",")
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

        segments = []
        for block in srt_text.strip().split("\n\n"):
            lines = block.split("\n")
            if len(lines) >= 3:
                start_str, end_str = lines[1].split(" --> ")
                text = " ".join(lines[2:])
                segments.append({
                    "inicio": parse_ts(start_str),
                    "fim": parse_ts(end_str),
                    "texto": text
                })

        return jsonify(duracao_total=segments[-1]["fim"], transcricao=segments)

    except Exception as e:
        return jsonify(error=str(e)), 500

    finally:
        try:
            audio_file.close()
        except:
            pass


# -- Endpoint /gerar_csv
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json() or {}
    transcricao = data.get("transcricao", [])
    prompts     = data.get("prompts", [])
    descricao   = data.get("descricao", "")

    # detecta único MP3 se não especificado
    mp3_filename = data.get("mp3_filename")
    if not mp3_filename:
        mp3s = list(AUDIO_DIR.glob("*.mp3"))
        if len(mp3s) == 1:
            mp3_filename = mp3s[0].name
        else:
            return jsonify(error="mp3_filename obrigatório ou múltiplos .mp3 encontrados"), 400

    slug = data.get("slug", Path(mp3_filename).stem)
    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    mp3_path = AUDIO_DIR / mp3_filename
    if not mp3_path.exists():
        return jsonify(error="MP3 não encontrado"), 400

    drive    = get_drive_service()
    pasta_id = criar_pasta_drive(slug, drive)

    # gera CSV, SRT, TXT
    csv_path = CSV_DIR / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"

    NEG_PROMPT = (
        "low quality, overexposed, underexposed, extra limbs, extra fingers, "
        "missing fingers, disfigured, deformed, bad anatomy"
    )
    header = [
        "PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL",
        "SEED_NUMBER","RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for seg, prm in zip(transcricao, prompts):
            sec = int(round(seg["inicio"]))
            prompt_final = f"{sec} - Painting style: Traditional watercolor, with soft brush strokes and handmade paper texture. {prm}"
            w.writerow([
                prompt_final, "PRIVATE", "9:16", "ON", "3.0","", "TURBO",
                NEG_PROMPT, "AUTO", ""
            ])

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(transcricao, 1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto'].strip()}\n\n")

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(descricao.strip())

    # envia para o Drive
    upload_arquivo_drive(csv_path, "imagens.csv", pasta_id, drive)
    upload_arquivo_drive(srt_path, "legenda.srt", pasta_id, drive)
    upload_arquivo_drive(txt_path, "descricao.txt", pasta_id, drive)
    upload_arquivo_drive(mp3_path, "voz.mp3", pasta_id, drive)

    return jsonify(folder_url=f"https://drive.google.com/drive/folders/{pasta_id}")


# -- Endpoint /upload_zip
@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify(error="Campo 'zip' obrigatório."), 400

    # detecta pasta única de projeto
    projetos = [p for p in FILES_DIR.iterdir() if p.is_dir() and not p.name.endswith("_raw")]
    if len(projetos) != 1:
        return jsonify(error="Deve haver exatamente 1 pasta de projeto existente"), 400

    slug       = projetos[0].name
    temp_dir   = FILES_DIR / f"{slug}_raw"
    output_dir = FILES_DIR / slug
    temp_dir.mkdir(exist_ok=True)
    output_dir.mkdir(exist_ok=True)

    zip_path = temp_dir / "imagens.zip"
    file.save(zip_path)

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(temp_dir)

    imagens = [f for f in temp_dir.glob("*") if f.suffix.lower() in (".jpg", ".jpeg", ".png")]
    if not imagens:
        return jsonify(error="Nenhuma imagem encontrada no ZIP."), 400

    # lê prompts do CSV
    csv_path = CSV_DIR / f"{slug}.csv"
    if not csv_path.exists():
        return jsonify(error="CSV não encontrado para este projeto."), 400

    prompts = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            prompts.append(row["PROMPT"].split(" - ", 1)[-1].strip())

    # seleciona imagem por similaridade de nome
    usadas = []
    for i, prm in enumerate(prompts):
        melhor = max(imagens, key=lambda img: prm.lower() in img.stem.lower())
        dest = output_dir / f"{i:02d}_{melhor.name}"
        melhor.rename(dest)
        imagens.remove(melhor)
        usadas.append(dest.name)

    return jsonify(ok=True, slug=slug, usadas=usadas, total_prompts=len(prompts))


# -- Endpoint /montar_video
@app.route("/montar_video", methods=["POST"])
def montar_video():
    from difflib import SequenceMatcher

    def sim(a, b):
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    data = request.get_json(force=True)
    folder_id = data.get("folder_id")

    # único slug existente
    subs = [p.name for p in FILES_DIR.iterdir() if p.is_dir() and not p.name.endswith("_raw")]
    if len(subs) != 1:
        return jsonify(error="Esperado exatamente 1 pasta de imagens"), 400
    slug = subs[0]

    pasta = FILES_DIR / slug
    imagens = sorted(pasta.glob("*"))
    if not imagens:
        return jsonify(error="Nenhuma imagem na pasta"), 400

    # único áudio e CSV e SRT
    audio_path = next(AUDIO_DIR.glob("*.mp3"), None)
    csv_path   = next(CSV_DIR.glob("*.csv"), None)
    srt_path   = next(FILES_DIR.glob("*.srt"), None)
    if not audio_path or not csv_path or not srt_path:
        return jsonify(error="MP3, CSV ou SRT faltando"), 400

    # lê prompts
    prompts = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        r = csv.reader(f); next(r)
        for row in r:
            prompts.append(row[0].split(" - ", 1)[-1].strip())

    # lê transcrição SRT
    transcricao = []
    with open(srt_path, encoding="utf-8") as f:
        blocks = f.read().strip().split("\n\n")
        for blk in blocks:
            parts = blk.split("\n")
            if len(parts) >= 3:
                t0, t1 = parts[1].split(" --> ")
                def to_sec(ts):
                    h,m,rest=ts.split(":"); s,ms=rest.split(",")
                    return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
                text=" ".join(parts[2:])
                transcricao.append({"inicio":to_sec(t0),"fim":to_sec(t1),"texto":text})

    audio_clip = AudioFileClip(str(audio_path))
    clips = []
    for i, seg in enumerate(transcricao):
        dur = seg["fim"] - seg["inicio"]
        txt = seg["texto"]
        img = ImageClip(str(imagens[i % len(imagens)])).resize(height=720)\
            .crop(x_center="center", width=1280).set_duration(dur)
        zoom = img.resize(lambda t: 1 + 0.02 * t)
        leg = TextClip(txt.upper(), fontsize=60, font="DejaVu-Sans-Bold",
                       color="white", stroke_color="black", stroke_width=2,
                       size=(1280,None), method="caption")\
            .set_duration(dur).set_position(("center","bottom"))
        grain = make_grain().set_opacity(0.05).set_duration(dur)
        luz   = VideoFileClip("sobrepor.mp4").resize((1280,720))\
            .set_opacity(0.07).set_duration(dur)
        marca = ImageClip("sobrepor.png").resize(height=100)\
            .set_opacity(1).set_duration(dur).set_position((20,20))
        comp = CompositeVideoClip([zoom, grain, luz, marca, leg], size=(1280,720))
        clips.append(comp)

    # encerramento
    end = ImageClip("fechamento.png").resize(height=720)\
        .crop(x_center="center", width=1280).set_duration(3)
    gf = make_grain().set_opacity(0.05).set_duration(3)
    lf = VideoFileClip("sobrepor.mp4").resize((1280,720))\
        .set_opacity(0.07).set_duration(3)
    encerr = CompositeVideoClip([end, gf, lf], size=(1280,720))

    final = concatenate_videoclips(clips + [encerr]).set_audio(audio_clip)
    out = FILES_DIR / f"{slug}.mp4"
    final.write_videofile(str(out), fps=24, codec="libx264", audio_codec="aac")

    drive = get_drive_service()
    upload_arquivo_drive(out, "video_final.mp4", folder_id, drive)
    return jsonify(ok=True, video=f"https://drive.google.com/drive/folders/{folder_id}")


# -- Rotas estáticas para servir arquivos e plugin
@app.route("/audio/<path:fn>")
def serve_audio(fn):
    return send_from_directory(AUDIO_DIR, fn)

@app.route("/csv/<path:fn>")
def serve_csv(fn):
    return send_from_directory(CSV_DIR, fn)

@app.route("/downloads/<path:fn>")
def serve_download(fn):
    return send_from_directory(FILES_DIR, fn)

@app.route("/.well-known/openapi.json")
def serve_openapi():
    return send_from_directory(".well-known", "openapi.json", mimetype="application/json")

@app.route("/.well-known/ai-plugin.json")
def serve_ai_plugin():
    return send_from_directory(".well-known", "ai-plugin.json", mimetype="application/json")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)