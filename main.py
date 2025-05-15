import os
import io
import csv
import re
import uuid
import zipfile
import requests
import unidecode
import tempfile
import shutil
import ffmpeg
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import openai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# — Configurações de pastas —
BASE       = Path(".")
AUDIO_DIR  = BASE / "audio"
CSV_DIR    = BASE / "csv"
FILES_DIR  = BASE / "downloads"
for d in (AUDIO_DIR, CSV_DIR, FILES_DIR):
    d.mkdir(parents=True, exist_ok=True)

GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY        = os.getenv("ELEVENLABS_API_KEY")
openai.api_key        = os.getenv("OPENAI_API_KEY")

# — Helpers de Drive —
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_pasta_drive(nome, drive):
    meta = {'name': nome, 'mimeType': 'application/vnd.google-apps.folder',
            'parents': [GOOGLE_DRIVE_FOLDER_ID]}
    folder = drive.files().create(body=meta, fields='id').execute()
    return folder['id']

def upload_arquivo_drive(path, nome, folder_id, drive):
    meta = {'name': nome, 'parents': [folder_id]}
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(body=meta, media_body=media).execute()

# — Outros Helpers —
def slugify(text, limit=30):
    txt = unidecode.unidecode(text)
    txt = re.sub(r"[^\w\s]", "", txt)
    return txt.strip().replace(" ", "_").lower()[:limit]

def elevenlabs_tts(text):
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "voice_settings": {
            "stability": 0.60,
            "similarity_boost": 0.90,
            "style": 0.15,
            "use_speaker_boost": True
        },
        "model_id": "eleven_multilingual_v2",
        "voice_id": "cwIsrQsWEVTols6slKYN"
    }
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
        headers=headers, json=payload
    )
    r.raise_for_status()
    return r.content

# — Rotas públicas —
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
def serve_download(fn):
    return send_from_directory(FILES_DIR, fn)

# — /falar — gera MP3 com ElevenLabs —
@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug     = slugify(texto)
    filename = f"{slug}.mp3"
    outpath  = AUDIO_DIR / filename

    try:
        audio_bytes = elevenlabs_tts(texto)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    with open(outpath, "wb") as f:
        f.write(audio_bytes)

    return jsonify(
        audio_url = request.url_root.rstrip("/") + f"/audio/{filename}",
        filename  = filename,
        slug      = slug
    )

# — /transcrever — usa Whisper para SRT e JSON —
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    if audio_url.startswith(request.url_root.rstrip("/")):
        fname = audio_url.rsplit("/audio/",1)[-1]
        file  = open(AUDIO_DIR / fname, "rb")
    else:
        resp = requests.get(audio_url, timeout=60)
        resp.raise_for_status()
        file = io.BytesIO(resp.content); file.name = audio_url.rsplit("/",1)[-1]

    try:
        srt = openai.audio.transcriptions.create(
            model="whisper-1", file=file, response_format="srt"
        )

        def parse_ts(ts):
            h,m,rest = ts.split(":")
            s,ms     = rest.split(",")
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

        segs = []
        for bloco in srt.strip().split("\n\n"):
            lines = bloco.split("\n")
            if len(lines)<3: continue
            st, en = lines[1].split(" --> ")
            txt    = " ".join(lines[2:])
            segs.append({"inicio": parse_ts(st), "fim": parse_ts(en), "texto": txt})

        return jsonify(duracao_total=segs[-1]["fim"], transcricao=segs)
    except Exception as e:
        return jsonify(error=str(e)), 500
    finally:
        try: file.close()
        except: pass

# — /gerar_csv — prepara CSV, SRT, TXT e faz upload —
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data           = request.get_json() or {}
    transcricao    = data.get("transcricao", [])
    prompts        = data.get("prompts", [])
    descricao      = data.get("descricao", "")
    texto_original = data.get("texto_original", "")

    slug = slugify(texto_original or descricao)
    if not transcricao or not prompts or len(transcricao)!=len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    drive    = get_drive_service()
    folderId = criar_pasta_drive(slug, drive)

    csv_path = CSV_DIR  / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"
    mp3_path = AUDIO_DIR / f"{slug}.mp3"

    # CSV
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["TIME","PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL","SEED","RENDERING","NEGATIVE","STYLE","PALETTE"])
        neg = "low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy"
        for seg,p in zip(transcricao,prompts):
            t = int(seg["inicio"])
            w.writerow([t, f"{t} - {p}", "PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT
    def format_ts(s):
        ms = int((s%1)*1000); h=int(s//3600); m=int((s%3600)//60); sec=int(s%60)
        return f"{h:02}:{m:02}:{sec:02},{ms:03}"
    with open(srt_path, "w", encoding="utf-8") as f:
        for i,seg in enumerate(transcricao,1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")

    # TXT
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(descricao.strip())

    # Uploads
    upload_arquivo_drive(csv_path, f"{slug}.csv", folderId, drive)
    upload_arquivo_drive(srt_path, f"{slug}.srt", folderId, drive)
    upload_arquivo_drive(txt_path, f"{slug}.txt", folderId, drive)
    if mp3_path.exists():
        upload_arquivo_drive(mp3_path, f"{slug}.mp3", folderId, drive)

    return jsonify(slug=slug, folder_url=f"https://drive.google.com/drive/folders/{folderId}")

# — /upload_zip — descompacta ZIP de imagens —
@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify(error="Campo 'zip' obrigatório."), 400

    projs = [p for p in FILES_DIR.iterdir() if p.is_dir()]
    if len(projs)!=1:
        return jsonify(error="Espere uma única pasta de projeto."), 400
    slug = projs[0].name
    tmp  = FILES_DIR / f"{slug}_raw"
    out  = FILES_DIR / slug
    tmp.mkdir(exist_ok=True); out.mkdir(exist_ok=True)

    zip_path = tmp/"imgs.zip"
    file.save(zip_path)
    with zipfile.ZipFile(zip_path) as z: z.extractall(tmp)

    imgs = [f for f in tmp.iterdir() if f.suffix.lower() in (".jpg",".jpeg",".png")]
    if not imgs:
        return jsonify(error="Nenhuma imagem no ZIP."), 400

    # lê prompts do CSV
    csv_path = CSV_DIR / f"{slug}.csv"
    if not csv_path.exists():
        return jsonify(error="CSV não encontrado."), 400
    prompts=[]
    with open(csv_path, encoding="utf-8") as f:
        reader=csv.DictReader(f)
        for r in reader:
            prompts.append(r["PROMPT"].split(" - ",1)[-1])

    # placeholder de seleção: sempre a primeira imagem
    selecionadas = []
    for idx,p in enumerate(prompts):
        img = imgs[0]
        dst = out / f"{idx:02d}_{img.name}"
        img.rename(dst)
        selecionadas.append(dst.name)

    return jsonify(ok=True, slug=slug, usadas=selecionadas)

# — /montar_video — gera e faz upload do MP4 final —
@app.route("/montar_video", methods=["POST"])
def montar_video():
    data      = request.get_json(force=True) or {}
    slug      = data.get("slug")
    folder_id = data.get("folder_id")

    try:
        # 1) Carrega SRT
        srt_path = FILES_DIR / f"{slug}.srt"
        def parse_ts(ts):
            h,m,rest = ts.split(":"); s,ms = rest.split(",")
            return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

        segmentos = []
        with open(srt_path, encoding="utf-8") as f:
            for blk in f.read().strip().split("\n\n"):
                lines = blk.split("\n")
                if len(lines)>=3:
                    st,en = lines[1].split(" --> ")
                    txt   = lines[2]
                    segmentos.append({
                        "inicio": parse_ts(st),
                        "fim":    parse_ts(en),
                        "texto":  txt
                    })

        # 2) Lista de imagens
        img_dir = FILES_DIR / slug
        imagens = sorted([p for p in img_dir.iterdir() if p.suffix.lower() in (".jpg",".jpeg",".png")])
        if not imagens:
            return jsonify(error="Sem imagens."), 400

        # 3) Cria vídeos temporários para cada segmento
        tmpdir = Path(tempfile.mkdtemp(prefix="seg_"))
        seg_files = []
        for i,seg in enumerate(segmentos):
            dur = seg["fim"] - seg["inicio"]
            img = imagens[i%len(imagens)]
            out_seg = tmpdir/f"seg_{i}.mp4"

            (
                ffmpeg
                .input(str(img), loop=1, t=dur, framerate=24)
                .filter('zoompan',
                        z="zoom+0.001",   # zoom incremental
                        d=int(dur*24),   # frames = duration * fps
                        s="1280x720")
                .filter('drawtext',
                        text=seg["texto"],
                        fontfile="/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                        fontsize=60,
                        fontcolor="white",
                        x="(w-text_w)/2",
                        y="h-text_h-20",
                        shadowcolor="black",
                        shadowx=2,
                        shadowy=2)
                .filter('noise', 'alls', '20')  # granulação leve
                .output(str(out_seg), vcodec='libx264', pix_fmt='yuv420p')
                .overwrite_output()
                .run(quiet=True)
            )
            seg_files.append(str(out_seg))

        # 4) Monta arquivo de concat
        concat_txt = tmpdir/"concat.txt"
        with open(concat_txt, "w") as f:
            for p in seg_files:
                f.write(f"file '{p}'\n")

        # 5) Concatena vídeo e adiciona áudio
        mp3_path = AUDIO_DIR / f"{slug}.mp3"
        final_out = FILES_DIR / f"{slug}.mp4"

        video_in = ffmpeg.input(str(concat_txt), format='concat', safe=0)
        audio_in = ffmpeg.input(str(mp3_path))
        (
            ffmpeg
            .output(video_in.video, audio_in.audio, str(final_out),
                    vcodec='libx264', acodec='aac', strict='experimental')
            .overwrite_output()
            .run(quiet=True)
        )

        # 6) Upload final
        drive = get_drive_service()
        upload_arquivo_drive(final_out, f"{slug}.mp4", folder_id, drive)

        # limpa temporários
        shutil.rmtree(tmpdir)

        return jsonify(video_url=f"https://drive.google.com/drive/folders/{folder_id}")

    except Exception as e:
        return jsonify(error=str(e)), 500

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)
