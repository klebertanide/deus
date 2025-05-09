import os, uuid, io, csv, zipfile, base64
import requests
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
from datetime import datetime

load_dotenv()
app = Flask(__name__)

# Pastas
BASE = Path(".")
AUDIO_DIR = BASE / "downloads/audio"
CSV_DIR = BASE / "downloads/csv"
TXT_DIR = BASE / "downloads/txt"
ZIP_DIR = BASE / "downloads/zip"
for d in [AUDIO_DIR, CSV_DIR, TXT_DIR, ZIP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Chaves
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_KEY)

# GitHub
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO")  # Ex: "usuario/repositorio"
GITHUB_BRANCH = os.getenv("GITHUB_BRANCH", "main")

# ===== Upload GitHub =====
def upload_to_github(filepath: Path, github_path: str):
    if not GITHUB_TOKEN or not GITHUB_REPO:
        return None

    with open(filepath, "rb") as f:
        content = base64.b64encode(f.read()).decode("utf-8")

    api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{github_path}"
    headers = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json"
    }

    commit_message = f"Upload automático de {filepath.name} ({datetime.utcnow().isoformat()}Z)"
    data = {
        "message": commit_message,
        "content": content,
        "branch": GITHUB_BRANCH
    }

    response = requests.put(api_url, headers=headers, json=data)
    if response.status_code in [200, 201]:
        return response.json()["content"]["html_url"]
    return None

# ===== ElevenLabs TTS =====
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

# ===== /falar =====
@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json(force=True, silent=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify({"error": "campo 'texto' obrigatório"}), 400

    audio_bytes = elevenlabs_tts(texto)
    filename = f"{uuid.uuid4()}.mp3"
    path = AUDIO_DIR / filename

    with open(path, "wb") as f:
        f.write(audio_bytes)

    # GitHub backup opcional
    github_url = upload_to_github(path, f"audio/{filename}")

    url_base = request.url_root.rstrip("/")
    return jsonify({
        "audio_url": f"{url_base}/downloads/audio/{filename}",
        "github_backup": github_url
    })

# ===== /transcrever =====
def _get_audio_file(audio_url):
    if audio_url.startswith(request.url_root.rstrip('/')):
        fname = audio_url.split('/downloads/audio/')[-1]
        p = AUDIO_DIR / fname
        if p.exists():
            return open(p, 'rb')
    resp = requests.get(audio_url, timeout=60)
    resp.raise_for_status()
    buf = io.BytesIO(resp.content)
    buf.name = "remote.mp3"
    return buf

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True, silent=True) or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error": "campo 'audio_url' obrigatório"}), 400
    try:
        audio_file = _get_audio_file(audio_url)
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )
        duration = transcript.duration
        segments = [
            {"inicio": seg.start, "fim": seg.end, "texto": seg.text}
            for seg in transcript.segments
        ]
        return jsonify({"duracao_total": duration, "transcricao": segments})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            audio_file.close()
        except:
            pass

# ===== /gerar_csv =====
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json(force=True, silent=True) or {}
    transcricao = data.get("transcricao")
    prompts = data.get("prompts", [])

    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify({"error": "É necessário fornecer listas 'transcricao' e 'prompts' com o mesmo tamanho."}), 400

    filename = f"{uuid.uuid4()}.csv"
    path = CSV_DIR / filename

    header = [
        "PROMPT", "VISIBILITY", "ASPECT_RATIO", "MAGIC_PROMPT", "MODEL",
        "SEED_NUMBER", "RENDERING", "NEGATIVE_PROMPT", "STYLE", "COLOR_PALETTE"
    ]
    negative_prompt = "low quality, overexposed, underexposed, extra limbs, extra fingers, missing fingers, disfigured, deformed, bad anatomy, crooked eyes, mutated hands"

    with open(path, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for bloco, prompt in zip(transcricao, prompts):
            segundo = int(round(bloco.get("inicio", 0)))
            prompt_final = f'{segundo} - Painting style: Traditional Japanese oriental watercolor, with soft brush strokes and handmade paper texture. {prompt}'
            if "," in prompt_final:
                prompt_final = f'"{prompt_final}"'
            writer.writerow([
                prompt_final, "PRIVATE", "9:16", "ON", "3.0", "", "TURBO",
                negative_prompt, "AUTO", ""
            ])

    # GitHub opcional
    github_url = upload_to_github(path, f"csv/{filename}")

    url_base = request.url_root.rstrip("/")
    return jsonify({
        "csv_url": f"{url_base}/downloads/csv/{filename}",
        "github_backup": github_url
    })

# ===== Servir arquivos públicos =====
@app.route("/downloads/<path:folder>/<path:filename>")
def baixar_download(folder, filename):
    folder_path = BASE / "downloads" / folder
    return send_from_directory(folder_path, filename)

# ===== Run local =====
if __name__ == "__main__":
    app.run(debug=True)