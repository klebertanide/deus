# Bloco 1 - Imports e Configurações Iniciais
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

BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR = BASE / "csv"
FILES_DIR = BASE / "downloads"
for d in [AUDIO_DIR, CSV_DIR, FILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_KEY

# Bloco 2 - Utilitários de Drive e Texto

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(texto, limite=30):
    texto = unidecode.unidecode(texto)
    texto = re.sub(r"(?i)^deus\\s+", "", texto)
    texto = re.sub(r"[^\w\s]", "", texto)
    texto = texto.strip().replace(" ", "_")
    return texto[:limite].lower()

def criar_pasta_drive(slug, drive):
    metadata = {
        "name": f"deus_{slug}",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [GOOGLE_DRIVE_FOLDER_ID]
    }
    pasta = drive.files().create(body=metadata, fields="id").execute()
    return pasta.get("id")

def upload_arquivo_drive(filepath, filename, folder_id, drive):
    file_metadata = {
        "name": filename,
        "parents": [folder_id]
    }
    media = MediaFileUpload(str(filepath), resumable=True)
    file = drive.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return file.get("id")

# Bloco 3 - Utilidades Visuais e Similaridade

def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280, 720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity, 128+intensity, (size[1], size[0], 1), dtype=np.uint8)
        noise = np.repeat(noise, 3, axis=2)
        return noise
    return VideoClip(frame, duration=1).set_fps(24)

def selecionar_imagem_mais_similar(prompt, imagens):
    import re
    from sentence_transformers import SentenceTransformer, util

    model = SentenceTransformer("clip-ViT-B-32")
    prompt_emb = model.encode(prompt, convert_to_tensor=True)

    melhor_score = -1
    melhor_img = None

    for img in imagens:
        nome_limpo = re.sub(r"[^\w\s]", " ", img.stem)
        nome_emb = model.encode(nome_limpo, convert_to_tensor=True)
        score = util.cos_sim(prompt_emb, nome_emb).item()

        if score > melhor_score:
            melhor_score = score
            melhor_img = img

    return melhor_img

# Bloco 4 - Geração de Áudio

def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN", retries=3):
    def enviar_requisicao(payload, desc):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
        for attempt in range(retries):
            try:
                response = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
                if not response.ok:
                    print(f"[Erro ElevenLabs] {desc} status {response.status_code}")
                response.raise_for_status()
                return response.content
            except requests.RequestException as e:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise RuntimeError(f"Falha ElevenLabs ({desc})") from e

    p1 = {"text": text, "voice_settings": {"stability": 0.6, "similarity_boost": 0.9, "style": 0.2}}
    try:
        audio = enviar_requisicao(p1, "com style")
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise ValueError("Resposta vazia")
        return audio
    except Exception:
        p2 = {"text": text, "voice_settings": {"stability": 0.6, "similarity_boost": 0.9}}
        audio = enviar_requisicao(p2, "sem style")
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise ValueError("Resposta vazia")
        return audio

# Bloco 5 - Endpoints Básicos

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

# Bloco 6 - Endpoint /falar
@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json() or {}
    texto = data.get("texto")
    if not texto:
        return jsonify({"error": "campo 'texto' obrigatório"}), 400

    slug = slugify(texto)
    filename = f"{slug}.mp3"
    path = AUDIO_DIR / filename

    try:
        audio_bytes = elevenlabs_tts(texto)
    except Exception as e:
        return jsonify({"error": "falha ElevenLabs", "detalhe": str(e)}), 500

    with open(path, "wb") as f:
        f.write(audio_bytes)

    return jsonify({
        "audio_url": request.url_root.rstrip("/") + f"/audio/{filename}",
        "filename": filename,
        "slug": slug
    })

# Bloco 7 - Endpoint /transcrever
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error": "campo 'audio_url' obrigatório"}), 400

    try:
        # abre ou baixa o MP3
        if audio_url.startswith(request.url_root.rstrip("/")):
            fname = audio_url.rsplit("/audio/", 1)[-1]
            audio_file = open(AUDIO_DIR / fname, "rb")
        else:
            resp = requests.get(audio_url, timeout=60)
            resp.raise_for_status()
            audio_file = io.BytesIO(resp.content)
            audio_file.name = "audio.mp3"

        # 1) gerar SRT
        srt_text = openai.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="srt"
        )

        # 2) converter timestamps
        def parse_ts(ts):
            h, m, rest = ts.split(":")
            s, ms = rest.split(",")
            return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000

        # 3) montar lista de segmentos
        segments = []
        for block in srt_text.strip().split("\n\n"):
            lines = block.split("\n")
            if len(lines) >= 3:
                start_str, end_str = lines[1].split(" --> ")
                text = " ".join(lines[2:])
                segments.append({
                    "inicio": parse_ts(start_str),
                    "fim":   parse_ts(end_str),
                    "texto": text
                })

        return jsonify({
            "duracao_total": segments[-1]["fim"],
            "transcricao": segments
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        try:
            audio_file.close()
        except:
            pass

# Bloco 8 - Endpoint /gerar_csv

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json() or {}
    transcricao = data.get("transcricao", [])
    prompts = data.get("prompts", [])
    descricao = data.get("descricao", "")
   mp3_filename = data.get("mp3_filename")
if not mp3_filename:
    mp3s = list(AUDIO_DIR.glob("*.mp3"))
    if len(mp3s) == 1:
        mp3_filename = mp3s[0].name
    elif len(mp3s) == 0:
        return jsonify({"error": "Nenhum arquivo .mp3 encontrado na pasta."}), 400
    else:
        return jsonify({"error": "Vários arquivos .mp3 encontrados. Informe qual usar com 'mp3_filename'."}), 400

    slug = data.get("slug", Path(mp3_filename).stem)

    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify({"error": "transcricao+prompts inválidos"}), 400

    mp3_path = AUDIO_DIR / mp3_filename
    if not mp3_path.exists():
        return jsonify({"error": "MP3 não encontrado"}), 400

    drive = get_drive_service()
    pasta_id = criar_pasta_drive(slug, drive)

    csv_path = CSV_DIR / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"

    # CSV
    header = ["PROMPT", "VISIBILITY", "ASPECT_RATIO", "MAGIC_PROMPT", "MODEL",
              "SEED_NUMBER", "RENDERING", "NEGATIVE_PROMPT", "STYLE", "COLOR_PALETTE"]
    neg = "low quality, overexposed, underexposed, extra limbs, extra fingers, missing fingers, disfigured, deformed, bad anatomy"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        for seg, prompt in zip(transcricao, prompts):
            sec = int(round(seg["inicio"]))
            pf = f"{sec} - Painting style: Traditional watercolor, with soft brush strokes and handmade paper texture. {prompt}"
            w.writerow([pf, "PRIVATE", "9:16", "ON", "3.0", "", "TURBO", neg, "AUTO", ""])

    # SRT
    with open(srt_path, "w", encoding="utf-8") as s:
        for i, seg in enumerate(transcricao, 1):
            s.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto'].strip()}\n\n")

    # TXT
    with open(txt_path, "w", encoding="utf-8") as t:
        t.write(descricao.strip())

    # Uploads
    upload_arquivo_drive(csv_path, "imagens.csv", pasta_id, drive)
    upload_arquivo_drive(srt_path, "legenda.srt", pasta_id, drive)
    upload_arquivo_drive(txt_path, "descricao.txt", pasta_id, drive)
    upload_arquivo_drive(mp3_path, "voz.mp3", pasta_id, drive)

    return jsonify({"folder_url": f"https://drive.google.com/drive/folders/{pasta_id}"})

# Bloco 9 - Upload ZIP e Seleção de Imagens
"/upload_zip": {
  "post": {
    "summary": "Faz upload de imagens .zip e associa automaticamente",
    "operationId": "uploadZip",
    "requestBody": {
      "required": true,
      "content": {
        "multipart/form-data": {
          "schema": {
            "type": "object",
            "properties": {
              "zip": {
                "type": "string",
                "format": "binary"
              }
            },
            "required": ["zip"]
          }
        }
      }
    },
    "responses": {
      "200": {
        "description": "Imagens processadas e selecionadas"
      }
    }
  }
}

# Bloco 10 - Montagem de Vídeo e Encerramento
@app.route("/montar_video", methods=["POST"])
def montar_video():
    from difflib import SequenceMatcher

    def similaridade(a, b):
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    data = request.get_json(force=True)
    slug = data.get("slug")
    folder_id = data.get("folder_id")

    pasta_local = FILES_DIR / slug
    imagens = sorted([
        f for f in pasta_local.iterdir()
        if f.suffix.lower() in ['.jpg', '.jpeg', '.png']
    ])

    mp3s = list(AUDIO_DIR.glob("*.mp3"))
    if not mp3s:
        return jsonify({"error": "Nenhum arquivo de áudio encontrado."}), 400
    audio_path = mp3s[0]

    srt_files = list(FILES_DIR.glob("*.srt"))
    if not srt_files:
        return jsonify({"error": "Nenhum arquivo de legenda .srt encontrado."}), 400
    transcricao_path = srt_files[0]

    csvs = list(CSV_DIR.glob("*.csv"))
    if not csvs:
        return jsonify({"error": "Nenhum arquivo CSV encontrado."}), 400
    csv_path = csvs[0]

    prompts = []
    with open(csv_path, newline='', encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if row:
                prompt = row[0].split(" - ", 1)[-1]
                prompts.append(prompt)

    associadas = []
    usadas = set()
    for prompt in prompts:
        melhor = max(
            [img for img in imagens if img not in usadas],
            key=lambda img: similaridade(prompt, img.stem),
            default=None
        )
        if melhor:
            associadas.append(melhor)
            usadas.add(melhor)
        else:
            associadas.append(imagens[0])

    with open(transcricao_path, encoding="utf-8") as f:
        blocos = f.read().strip().split("\n\n")
        transcricao = []
        for bloco in blocos:
            partes = bloco.split("\n")
            if len(partes) >= 3:
                tempos = partes[1].split(" --> ")
                inicio = sum(float(x) * 60**i for i, x in enumerate(reversed(tempos[0].replace(",", ".").split(":"))))
                fim = sum(float(x) * 60**i for i, x in enumerate(reversed(tempos[1].replace(",", ".").split(":"))))
                texto = " ".join(partes[2:])
                transcricao.append({"inicio": inicio, "fim": fim, "texto": texto})

    audio_clip = AudioFileClip(str(audio_path))
    clips = []

    for i, bloco in enumerate(transcricao):
        tempo = bloco["fim"] - bloco["inicio"]
        texto = bloco["texto"]
        img = ImageClip(str(associadas[i % len(associadas)])).resize(height=720).crop(x_center='center', width=1280).set_duration(tempo)
        zoom = img.resize(lambda t: 1 + 0.02 * t)
        legenda = TextClip(texto.upper(), fontsize=60, font='DejaVu-Sans-Bold', color='white',
                           stroke_color='black', stroke_width=2, size=(1280, None), method='caption'
                           ).set_duration(tempo).set_position(('center', 'bottom'))
        grain = make_grain().set_opacity(0.05).set_duration(tempo)
        luz = VideoFileClip("sobrepor.mp4").resize((1280, 720)).set_opacity(0.07).set_duration(tempo)
        marca = ImageClip("sobrepor.png").resize(height=100).set_position((20, 20)).set_opacity(1).set_duration(tempo)
        comp = CompositeVideoClip([zoom, grain, luz, marca, legenda], size=(1280, 720))
        clips.append(comp)

    encerramento_img = ImageClip("fechamento.png").resize(height=720).crop(x_center='center', width=1280).set_duration(3)
    luz_final = VideoFileClip("sobrepor.mp4").resize((1280, 720)).set_opacity(0.07).set_duration(3)
    grain_final = make_grain().set_opacity(0.05).set_duration(3)
    encerramento = CompositeVideoClip([encerramento_img, grain_final, luz_final], size=(1280, 720))

    final_video = concatenate_videoclips(clips + [encerramento]).set_audio(audio_clip)
    output_path = FILES_DIR / f"{slug}.mp4"
    final_video.write_videofile(str(output_path), fps=24, codec='libx264', audio_codec='aac')

    drive = get_drive_service()
    upload_arquivo_drive(output_path, "video_final.mp4", folder_id, drive)

    return jsonify({ "ok": True, "video": f"https://drive.google.com/drive/folders/{folder_id}" })

# Bloco Extra - Documentação para GPT-4o
@app.route('/.well-known/ai-plugin.json')
def serve_ai_plugin():
    return send_from_directory('.well-known', 'ai-plugin.json', mimetype='application/json')

@app.route('/.well-known/openapi.json')
def serve_openapi():
    return send_from_directory('.well-known', 'openapi.json', mimetype='application/json')

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=True)
