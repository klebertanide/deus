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

# Google Drive
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# Pastas locais
BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR = BASE / "csv"
FILES_DIR = BASE / "downloads"
for d in [AUDIO_DIR, CSV_DIR, FILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Google Drive – pasta raiz
GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"

# Chaves
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_KEY

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(texto, limite=30):
    texto = unidecode.unidecode(texto)
    texto = re.sub(r"(?i)^deus\s+", "", texto)
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

def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

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

    # 1ª tentativa com style
    p1 = {"text": text, "voice_settings": {"stability": 0.6, "similarity_boost": 0.9, "style": 0.2}}
    try:
        audio = enviar_requisicao(p1, "com style")
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise ValueError("Resposta vazia")
        return audio
    except Exception:
        # 2ª tentativa sem style
        p2 = {"text": text, "voice_settings": {"stability": 0.6, "similarity_boost": 0.9}}
        audio = enviar_requisicao(p2, "sem style")
        if not isinstance(audio, (bytes, bytearray)) or not audio:
            raise ValueError("Resposta vazia")
        return audio

def make_grain(size=(1280, 720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity, 128+intensity, (size[1], size[0], 1), dtype=np.uint8)
        noise = np.repeat(noise, 3, axis=2)
        return noise
    return VideoClip(frame, duration=1).set_fps(24)

@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

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

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json() or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error": "campo 'audio_url' obrigatório"}), 400

    try:
        if audio_url.startswith(request.url_root.rstrip("/")):
            fname = audio_url.rsplit("/audio/", 1)[-1]
            p = AUDIO_DIR / fname
            audio_file = open(p, "rb")
        else:
            resp = requests.get(audio_url, timeout=60)
            resp.raise_for_status()
            audio_file = io.BytesIO(resp.content)
            audio_file.name = "audio.mp3"

        transcript = openai.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )

        duration = transcript.duration
        segments = [{"inicio": seg.start, "fim": seg.end, "texto": seg.text} for seg in transcript.segments]
        return jsonify({"duracao_total": duration, "transcricao": segments})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try: audio_file.close()
        except: pass

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json() or {}
    transcricao = data.get("transcricao", [])
    prompts = data.get("prompts", [])
    descricao = data.get("descricao", "")
    mp3_filename = data.get("mp3_filename")
    slug = data.get("slug", str(uuid.uuid4()))

    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify({"error": "transcricao+prompts inválidos"}), 400
    if not mp3_filename:
        return jsonify({"error": "campo 'mp3_filename' obrigatório"}), 400

    mp3_path = AUDIO_DIR / mp3_filename
    if not mp3_path.exists():
        return jsonify({"error": "MP3 não encontrado"}), 400

    drive = get_drive_service()
    pasta_id = criar_pasta_drive(slug, drive)

    csv_path = CSV_DIR / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"

    # CSV
    header = ["PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL",
              "SEED_NUMBER","RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"]
    neg = "low quality, overexposed, underexposed, extra limbs, extra fingers, missing fingers, disfigured, deformed, bad anatomy"
    with open(csv_path,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(header)
        for seg,prompt in zip(transcricao,prompts):
            sec=int(round(seg["inicio"]))
            pf=f"{sec} - Painting style: Traditional watercolor, with soft brush strokes and handmade paper texture. {prompt}"
            w.writerow([pf, "PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])

    # SRT
    with open(srt_path,"w",encoding="utf-8") as s:
        for i,seg in enumerate(transcricao,1):
            s.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto'].strip()}\n\n")

    # TXT
    with open(txt_path,"w",encoding="utf-8") as t:
        t.write(descricao.strip())

    # Uploads
    upload_arquivo_drive(csv_path,"imagens.csv",pasta_id,drive)
    upload_arquivo_drive(srt_path,"legenda.srt",pasta_id,drive)
    upload_arquivo_drive(txt_path,"descricao.txt",pasta_id,drive)
    upload_arquivo_drive(mp3_path,"voz.mp3",pasta_id,drive)

    return jsonify({"folder_url": f"https://drive.google.com/drive/folders/{pasta_id}"})

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify({"error": "Requer arquivo .zip com imagens."}), 400

    # Localizar pasta de projeto automaticamente (única existente)
    projetos = [p for p in FILES_DIR.iterdir() if p.is_dir() and p.name.endswith("_raw") is False]
    if not projetos:
        return jsonify({"error": "Nenhuma pasta de projeto encontrada."}), 400
    if len(projetos) > 1:
        return jsonify({"error": "Mais de uma pasta encontrada. Especifique manualmente."}), 400

    pasta_projeto = projetos[0]
    slug = pasta_projeto.name
    temp_dir = FILES_DIR / f"{slug}_raw"
    output_dir = FILES_DIR / slug
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Salvar ZIP
    zip_path = temp_dir / "imagens.zip"
    file.save(zip_path)

    # Extrair ZIP
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(temp_dir)

    imagens_extraidas = list(temp_dir.glob("*.png")) + list(temp_dir.glob("*.jpg")) + list(temp_dir.glob("*.jpeg"))
    if not imagens_extraidas:
        return jsonify({"error": "Nenhuma imagem encontrada no ZIP."}), 400

    # Ler prompts do CSV correspondente ao slug
    csv_path = CSV_DIR / f"{slug}.csv"
    if not csv_path.exists():
        return jsonify({"error": "CSV não encontrado para este projeto."}), 400

    prompts = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            prompt = row.get("PROMPT", "").split(". ")[-1].strip()
            if prompt:
                prompts.append(prompt)

    # Importar CLIP somente aqui (para evitar overhead se não for usado)
    import torch
    from PIL import Image
    from torchvision import transforms
    from clip import load as load_clip

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model, preprocess = load_clip("ViT-B/32", device=device)

    def embed_text(text):
        with torch.no_grad():
            return model.encode_text(clip.tokenize([text]).to(device)).float()

    def embed_image(image_path):
        with torch.no_grad():
            image = preprocess(Image.open(image_path)).unsqueeze(0).to(device)
            return model.encode_image(image).float()

    imagens_usadas = []
    for prompt in prompts:
        embed_p = embed_text(prompt)
        melhor_score = -1
        melhor_img = None
        for img_path in imagens_extraidas:
            try:
                embed_i = embed_image(img_path)
                score = torch.cosine_similarity(embed_p, embed_i).item()
                if score > melhor_score:
                    melhor_score = score
                    melhor_img = img_path
            except:
                continue

        if melhor_img:
            destino = output_dir / f"{len(imagens_usadas):02}.png"
            melhor_img.rename(destino)
            imagens_usadas.append(destino.name)

    if not imagens_usadas:
        return jsonify({"error": "Nenhuma imagem selecionada com base nos prompts."}), 500

    return jsonify({
        "ok": True,
        "slug": slug,
        "selecionadas": imagens_usadas,
        "pasta": str(output_dir)
    })

import os, uuid, io, csv, re, zipfile
import requests
import unidecode
import numpy as np
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
import openai
from moviepy.editor import (
    AudioFileClip, ImageClip, TextClip, CompositeVideoClip,
    concatenate_videoclips, VideoFileClip
)
from moviepy.video.VideoClip import VideoClip
from sentence_transformers import SentenceTransformer, util

# Google Drive
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# Pastas locais
BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR = BASE / "csv"
FILES_DIR = BASE / "downloads"
for d in [AUDIO_DIR, CSV_DIR, FILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Google Drive – pasta raiz
GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"

# Chaves
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_KEY

# Modelo CLIP para análise de similaridade entre prompt e imagem
clip_model = SentenceTransformer("clip-ViT-B-32")

def selecionar_imagem_mais_similar(prompt, imagens):
    prompt_emb = clip_model.encode(prompt, convert_to_tensor=True)
    melhor_score = -1
    melhor_img = None
    for img in imagens:
        nome_limpo = re.sub(r"[^\w\s]", " ", img.stem)
        nome_emb = clip_model.encode(nome_limpo, convert_to_tensor=True)
        score = util.cos_sim(prompt_emb, nome_emb).item()
        if score > melhor_score:
            melhor_score = score
            melhor_img = img
    return melhor_img

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file:
        return jsonify({"error": "Campo 'zip' obrigatório."}), 400

    # Detectar slug automaticamente (única pasta existente ou mais recente)
    pastas_existentes = sorted(FILES_DIR.glob("*/"), key=os.path.getmtime, reverse=True)
    if not pastas_existentes:
        return jsonify({"error": "Nenhuma pasta de projeto encontrada."}), 400

    slug_path = pastas_existentes[0]
    slug = slug_path.name.replace("_raw", "")
    temp_dir = FILES_DIR / f"{slug}_raw"
    output_dir = FILES_DIR / slug
    temp_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    zip_path = temp_dir / "imagens.zip"
    file.save(zip_path)

    # Extrair imagens
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(temp_dir)

    imagens = [f for f in temp_dir.glob("*.*") if f.suffix.lower() in [".jpg", ".jpeg", ".png"]]
    if not imagens:
        return jsonify({"error": "Nenhuma imagem encontrada no ZIP."}), 400

    # Localizar CSV
    csv_path = CSV_DIR / f"{slug}.csv"
    if not csv_path.exists():
        return jsonify({"error": f"CSV '{csv_path.name}' não encontrado."}), 400

    # Ler prompts do CSV
    prompts = []
    with open(csv_path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            prompt = row["PROMPT"]
            match = re.match(r"\d+ - .*?\. (.*)", prompt)
            if match:
                prompts.append(match.group(1))
            else:
                prompts.append(prompt)

    # Selecionar imagens com base nos prompts
    usadas = []
    for i, prompt in enumerate(prompts):
        img = selecionar_imagem_mais_similar(prompt, imagens)
        if img:
            destino = output_dir / f"{i:02d}_{img.name}"
            img.rename(destino)
            imagens.remove(img)
            usadas.append(destino.name)

    return jsonify({
        "ok": True,
        "slug": slug,
        "total_prompts": len(prompts),
        "total_imagens": len(usadas),
        "usadas": usadas
    })

@app.route("/montar_video", methods=["POST"])
def montar_video():
    data = request.get_json(force=True)
    slug = data.get("slug")
    transcricao = data.get("transcricao")
    folder_id = data.get("folder_id")

    pasta_local = FILES_DIR / slug
    imagens = sorted([
        f for f in pasta_local.iterdir()
        if f.suffix.lower() in ['.jpg', '.jpeg', '.png']
    ])
    if not imagens:
        return jsonify({"error": "Imagens não encontradas."}), 400

    audio_path = AUDIO_DIR / f"{slug}.mp3"
    if not audio_path.exists():
        return jsonify({"error": "Áudio não encontrado."}), 400

    audio_clip = AudioFileClip(str(audio_path))
    clips = []

    for i, bloco in enumerate(transcricao):
        tempo = bloco["fim"] - bloco["inicio"]
        texto = bloco["texto"]
        img = ImageClip(str(imagens[i])).resize(height=720).crop(x_center='center', width=1280).set_duration(tempo)
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

# Servir estáticos
@app.route("/audio/<path:fn>")
def servir_audio(fn): return send_from_directory(AUDIO_DIR,fn)
@app.route("/csv/<path:fn>")
def servir_csv(fn): return send_from_directory(CSV_DIR,fn)
@app.route("/downloads/<path:fn>")
def servir_down(fn): return send_from_directory(FILES_DIR,fn)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT",5000)), debug=True)