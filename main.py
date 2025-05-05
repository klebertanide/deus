import os, uuid, io, time, tempfile
import requests
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

# Playwright
from playwright.sync_api import sync_playwright

load_dotenv()

app = Flask(__name__)
AUDIO_DIR = Path(os.getenv("AUDIO_DIR", "audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

ZIP_DIR = Path(os.getenv("ZIP_DIR", "ideogram_zips"))
ZIP_DIR.mkdir(parents=True, exist_ok=True)

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
IDEO_COOKIE = os.getenv("IDEO_SESSION")

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
    audio_url = request.url_root.rstrip('/') + '/audio/' + filename
    return jsonify({"audio_url": audio_url})

def _get_audio_file(audio_url):
    if audio_url.startswith(request.url_root.rstrip('/')):
        fname = audio_url.split('/audio/')[-1]
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

@app.route("/audio/<path:filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg")

# -------------- IDEOGRAM ------------------------

def _create_batch_csv(prompts, csv_path):
    header = ("PROMPT,VISIBILITY,ASPECT_RATIO,MAGIC_PROMPT,MODEL,"
              "SEED_NUMBER,RENDERING,NEGATIVE_PROMPT,STYLE,COLOR_PALETTE\n")
    negative = ("low quality, overexposed, underexposed, extra limbs, extra fingers, "
                "missing fingers, disfigured, deformed, bad anatomy, crooked eyes, mutated hands")
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write(header)
        for p in prompts:
            row = f"\"{p}\",PRIVATE,1:1,ON,2a-turbo,,FAST,\"{negative}\",AUTO,\n"
            f.write(row)

def _upload_and_download_playwright(prompts):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Adiciona cookies
        if IDEO_COOKIE:
            for item in IDEO_COOKIE.split(";"):
                if "=" in item:
                    name, val = item.strip().split("=", 1)
                    context.add_cookies([{
                        "name": name,
                        "value": val,
                        "domain": ".ideogram.ai",
                        "path": "/"
                    }])

        csv_path = Path(tempfile.gettempdir()) / f"{uuid.uuid4()}.csv"
        _create_batch_csv(prompts, csv_path)

        page.goto("https://about.ideogram.ai/batch")
        input_file = page.locator("input[type='file']")
        input_file.set_input_files(str(csv_path))

        page.get_by_role("button", name="Generate").click()

        # Aguarda botão de download aparecer
        page.wait_for_selector("a:has-text('Download')", timeout=60000)
        with page.expect_download() as download_info:
            page.locator("a:has-text('Download')").click()
        download = download_info.value
        zip_path = ZIP_DIR / f"{uuid.uuid4()}.zip"
        download.save_as(str(zip_path))

        browser.close()
        return zip_path

@app.route("/ideogram", methods=["POST"])
def ideogram():
    data = request.get_json(force=True, silent=True) or {}
    prompts = data.get("prompts")
    if not prompts or not isinstance(prompts, list):
        return jsonify({"error": "campo 'prompts' deve ser lista"}), 400
    try:
        zip_path = _upload_and_download_playwright(prompts)
        static_dir = Path("static")
        static_dir.mkdir(exist_ok=True)
        dest = static_dir / zip_path.name
        zip_path.replace(dest)
        zip_url = request.url_root.rstrip('/') + '/static/' + dest.name
        return jsonify({"zip_url": zip_url})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ----------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))
