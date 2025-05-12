import os, uuid, io, csv, re
import requests
import unidecode
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

# Google Drive
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

load_dotenv()
app = Flask(__name__)

# Pastas locais
BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR = BASE / "csv"
FILES_DIR = BASE / "downloads"
for d in [AUDIO_DIR, CSV_DIR, FILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Google Drive – pasta raiz (a que você compartilhou com a conta de serviço)
GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"

# Chaves
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_KEY)

# Google Drive Auth
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(texto, limite=30):
    texto = unidecode.unidecode(texto)
    texto = re.sub(r"[^\w\s]", "", texto)
    texto = texto.strip().replace(" ", "_")
    return texto[:limite].lower()

# Upload para subpasta no Drive
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

# Utilitário para legenda SRT
def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

# ElevenLabs TTS
def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN"):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {
            "stability": 0.60,
            "similarity_boost": 0.90,
            "style": 0.15,
            "use_speaker_boost": True
        }
    }
    r = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
    r.raise_for_status()
    return r.content

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

    audio_bytes = elevenlabs_tts(texto)
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
            audio_file.name = "remote.mp3"

        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )

        duration = transcript.duration
        segments = [{"inicio": s.start, "fim": s.end, "texto": s.text} for s in transcript.segments]
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

    drive = get_drive_service()
    pasta_id = criar_pasta_drive(slug, drive)

    # Arquivos locais
    csv_path = CSV_DIR / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"
    mp3_path = AUDIO_DIR / mp3_filename if mp3_filename else None

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
            if "," in prompt_final:
                prompt_final = f'{prompt_final}'
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

    # Upload para o Google Drive
    upload_arquivo_drive(csv_path, "imagens.csv", pasta_id, drive)
    upload_arquivo_drive(srt_path, "legenda.srt", pasta_id, drive)
    upload_arquivo_drive(txt_path, "descricao.txt", pasta_id, drive)
    if mp3_path and mp3_path.exists():
        upload_arquivo_drive(mp3_path, "voz.mp3", pasta_id, drive)

    folder_url = f"https://drive.google.com/drive/folders/{pasta_id}"
    return jsonify({ "folder_url": folder_url })

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
