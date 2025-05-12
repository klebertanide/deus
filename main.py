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

# Google Drive Auth
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
    media = MediaFileUpload(filepath, resumable=True)
    file = drive.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return file.get("id")

def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

import time  # já deve estar importado no topo, se não estiver, adicione

def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN", retries=3):
    import time

    def enviar_requisicao(payload, tentativa_desc=""):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        headers = {
            "xi-api-key": ELEVEN_API_KEY,
            "Content-Type": "application/json"
        }

        print(f"\n[DEBUG] Iniciando requisição{tentativa_desc}")
        print(f"[DEBUG] URL: {url}")
        print(f"[DEBUG] PAYLOAD: {payload}")

        for attempt in range(retries):
            try:
                print(f"[TTS] Tentativa {attempt + 1}{tentativa_desc}...")
                response = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
                if not response.ok:
                    print(f"[Erro ElevenLabs] Código: {response.status_code}")
                    print(f"Resposta: {response.text[:300]}")
                response.raise_for_status()
                return response.content
            except requests.RequestException as e:
                print(f"[Erro ElevenLabs] {tentativa_desc} tentativa {attempt + 1} falhou: {e}")
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise RuntimeError(
                        f"Falha ao gerar áudio com ElevenLabs após múltiplas tentativas ({tentativa_desc})."
                    ) from e

    # 1ª tentativa com style
    payload_com_style = {
        "text": text,
        "voice_settings": {
            "stability": 0.6,
            "similarity_boost": 0.9,
            "style": 0.2
        }
    }

    try:
        result = enviar_requisicao(payload_com_style, " com style")
        if not result:
            raise ValueError("A resposta da API estava vazia (None)")
        return result
    except Exception as e:
        print(f"[TTS] Falha com 'style': {e}")
        print("[TTS] Tentando novamente sem 'style'...")

        # 2ª tentativa sem style
        payload_sem_style = {
            "text": text,
            "voice_settings": {
                "stability": 0.6,
                "similarity_boost": 0.9
            }
        }

        result = enviar_requisicao(payload_sem_style, " sem style")
        if not result:
            raise ValueError("A resposta da API estava vazia (None)")
        return result

def make_grain(size=(1280, 720), intensity=10):
    def make_frame(t):
        noise = np.random.randint(
            low=128 - intensity,
            high=128 + intensity,
            size=(size[1], size[0], 1),
            dtype=np.uint8
        )
        noise = np.repeat(noise, 3, axis=2)
        return noise
    return VideoClip(make_frame, duration=1).set_fps(24)

@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json(force=True, silent=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify({"error": "campo 'texto' obrigatório"}), 400

    slug = slugify(texto)
    filename = f"{slug}.mp3"
    path = AUDIO_DIR / filename

    try:
        audio_bytes = elevenlabs_tts(texto)
        if not audio_bytes:
            raise ValueError("Nenhum conteúdo de áudio foi retornado.")
    except Exception as e:
        return jsonify({
            "error": "Falha ao gerar áudio com ElevenLabs.",
            "detalhe": str(e)
        }), 500

    with open(path, "wb") as f:
        f.write(audio_bytes)

    audio_url = request.url_root.rstrip('/') + '/audio/' + filename
    return jsonify({
        "audio_url": audio_url,
        "filename": filename,
        "slug": slug
    })

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True, silent=True) or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error": "campo 'audio_url' obrigatório"}), 400

    try:
        # Baixa ou abre o arquivo de áudio
        if audio_url.startswith(request.url_root.rstrip('/')):
            fname = audio_url.split('/audio/')[-1]
            p = AUDIO_DIR / fname
            if not p.exists():
                raise Exception("Arquivo local não encontrado.")
            audio_file = open(p, 'rb')
        else:
            resp = requests.get(audio_url, timeout=60)
            resp.raise_for_status()
            audio_file = io.BytesIO(resp.content)
            audio_file.name = "audio.mp3"

        # Transcreve usando Whisper da OpenAI
        transcript = openai.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )

        # Extrai dados corretamente usando atributos (não colchetes)
        duration = transcript.duration
        segments = [
            {"inicio": s.start, "fim": s.end, "texto": s.text}
            for s in transcript.segments
        ]

        return jsonify({"duracao_total": duration, "transcricao": segments})

    except Exception as e:
        return jsonify({"error": str(e)}), 500

    finally:
        try:
            audio_file.close()
        except:
            pass
            
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json(force=True, silent=True) or {}
    transcricao = data.get("transcricao")
    prompts = data.get("prompts", [])
    descricao = data.get("descricao", "Descrição não fornecida")
    mp3_filename = data.get("mp3_filename")
    slug = data.get("slug", str(uuid.uuid4()))

    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify({"error": "É necessário fornecer listas 'transcricao' e 'prompts' com o mesmo tamanho."}), 400

    if not mp3_filename:
        return jsonify({"error": "Campo 'mp3_filename' é obrigatório."}), 400

    mp3_path = AUDIO_DIR / mp3_filename
    if not mp3_path.exists():
        return jsonify({"error": f"O arquivo MP3 '{mp3_filename}' não foi encontrado em /audio."}), 400

    drive = get_drive_service()
    pasta_id = criar_pasta_drive(slug, drive)

    csv_path = CSV_DIR / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"

    # CSV
    header = [
        "PROMPT", "VISIBILITY", "ASPECT_RATIO", "MAGIC_PROMPT", "MODEL",
        "SEED_NUMBER", "RENDERING", "NEGATIVE_PROMPT", "STYLE", "COLOR_PALETTE"
    ]
    negative_prompt = "low quality, overexposed, underexposed, extra limbs, extra fingers, missing fingers, disfigured, deformed, bad anatomy, crooked eyes, mutated hands"

    with open(csv_path, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for bloco, prompt in zip(transcricao, prompts):
            segundo = int(round(bloco.get("inicio", 0)))
            prompt_final = f'{segundo} - Painting style: Traditional watercolor, with soft brush strokes and handmade paper texture. {prompt}'
            writer.writerow([
                prompt_final, "PRIVATE", "9:16", "ON", "3.0", "", "TURBO",
                negative_prompt, "AUTO", ""
            ])

    # SRT
    with open(srt_path, "w", encoding="utf-8") as srt:
        for i, seg in enumerate(transcricao, 1):
            ini = format_ts(seg["inicio"])
            fim = format_ts(seg["fim"])
            text = seg["texto"].strip()
            srt.write(f"{i}\n{ini} --> {fim}\n{text}\n\n")

    # TXT
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(descricao.strip())

    # Upload
    upload_arquivo_drive(csv_path, "imagens.csv", pasta_id, drive)
    upload_arquivo_drive(srt_path, "legenda.srt", pasta_id, drive)
    upload_arquivo_drive(txt_path, "descricao.txt", pasta_id, drive)
    upload_arquivo_drive(mp3_path, "voz.mp3", pasta_id, drive)

    folder_url = f"https://drive.google.com/drive/folders/{pasta_id}"
    return jsonify({ "folder_url": folder_url })

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    slug = request.form.get("slug")

    if not file or not slug:
        return jsonify({"error": "Requer 'zip' e 'slug'."}), 400

    temp_dir = FILES_DIR / slug
    temp_dir.mkdir(parents=True, exist_ok=True)

    # Salva e descompacta o ZIP
    zip_path = temp_dir / "imagens.zip"
    file.save(zip_path)

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(temp_dir)

    # Busca imagens em qualquer subpasta
    imagens = sorted([
        p for p in temp_dir.rglob("*")
        if p.suffix.lower() in ['.jpg', '.jpeg', '.png']
    ], key=lambda x: x.name)

    if not imagens:
        return jsonify({"error": "Nenhuma imagem encontrada no ZIP."}), 400

    return jsonify({
        "ok": True,
        "total_imagens": len(imagens),
        "imagens": [str(p.name) for p in imagens],
        "path": str(temp_dir)
    })

@app.route("/montar_video", methods=["POST"])
def montar_video():
    data = request.get_json(force=True)
    slug = data.get("slug")
    transcricao = data.get("transcricao")
    folder_id = data.get("folder_id")

    pasta_local = FILES_DIR / slug
    imagens = sorted([f for f in pasta_local.iterdir() if f.suffix.lower() in ['.jpg', '.png'] and "sobrepor" not in f.name and "fechamento" not in f.name])
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

@app.route("/audio/<path:filename>")
def baixar_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

@app.route("/csv/<path:filename>")
def baixar_csv(filename):
    return send_from_directory(CSV_DIR, filename)

@app.route("/downloads/<path:filename>")
def baixar_download(filename):
    return send_from_directory(FILES_DIR, filename)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)
