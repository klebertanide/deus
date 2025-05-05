import os, uuid, io, time, tempfile, shutil
from pathlib import Path
import requests
from flask import Flask, request, jsonify, send_from_directory
from openai import OpenAI

# --- NOVOS imports / ajustes ------------------------------------------
from dotenv import load_dotenv
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
# ----------------------------------------------------------------------

load_dotenv()  # lê variáveis do .env local (se houver)

app = Flask(__name__)

# ----------- Áudio (ElevenLabs + Whisper) ------------------------------
AUDIO_DIR = Path(os.getenv("AUDIO_DIR", "audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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
            "use_speaker_boost": True,
        },
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
    path.write_bytes(audio_bytes)
    audio_url = request.url_root.rstrip("/") + "/audio/" + filename
    return jsonify({"audio_url": audio_url})

def _get_audio_file(audio_url):
    if audio_url.startswith(request.url_root.rstrip("/")):
        fname = audio_url.split("/audio/")[-1]
        p = AUDIO_DIR / fname
        if p.exists():
            return open(p, "rb")
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
            timestamp_granularities=["segment"],
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
        except Exception:
            pass

@app.route("/audio/<path:filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg")

# --------------- IDEOGRAM batch ---------------------------------------
IDEO_COOKIE = os.getenv("IDEO_SESSION")  # cookie "name=value; ..."
BATCH_URL = "https://about.ideogram.ai/batch"
ZIP_DIR = Path(os.getenv("ZIP_DIR", "ideogram_zips"))
ZIP_DIR.mkdir(parents=True, exist_ok=True)

NEGATIVE = ("low quality, overexposed, underexposed, extra limbs, extra fingers, "
            "missing fingers, disfigured, deformed, bad anatomy, crooked eyes, mutated hands")

CSV_HEADER = ("PROMPT,VISIBILITY,ASPECT_RATIO,MAGIC_PROMPT,MODEL,"
              "SEED_NUMBER,RENDERING,NEGATIVE_PROMPT,STYLE,COLOR_PALETTE\n")

def _create_batch_csv(prompts, csv_path):
    with open(csv_path, "w", encoding="utf-8") as f:
        f.write(CSV_HEADER)
        for p in prompts:
            row = f"\"{p}\",PRIVATE,1:1,ON,2a-turbo,,FAST,\"{NEGATIVE}\",AUTO,\n"
            f.write(row)

# ---------- Chrome finder ---------------------------------------------
def _launch_driver() -> uc.Chrome:
    chrome_path = (
        os.getenv("GOOGLE_CHROME_BIN")
        or shutil.which("google-chrome")
        or shutil.which("chromium-browser")
        or "/usr/bin/google-chrome"
    )
    if not chrome_path:
        raise RuntimeError("Chrome não encontrado. Instale google‑chrome‑stable ou defina GOOGLE_CHROME_BIN")

    opts = Options()
    opts.binary_location = chrome_path
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")

    prefs = {"download.default_directory": str(ZIP_DIR.resolve())}
    opts.add_experimental_option("prefs", prefs)

    return uc.Chrome(options=opts)
# ----------------------------------------------------------------------

def _upload_and_download(csv_path):
    driver = _launch_driver()
    try:
        # injeta cookie de sessão
        driver.get("https://ideogram.ai")
        for item in IDEO_COOKIE.split(";"):
            if "=" in item:
                name, val = item.strip().split("=", 1)
                driver.add_cookie({"name": name, "value": val, "domain": ".ideogram.ai"})
        driver.get(BATCH_URL)

        # upload CSV
        input_box = WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type=file]'))
        )
        input_box.send_keys(str(csv_path))
        driver.find_element(By.XPATH, "//button[contains(.,'Generate')]").click()

        # espera botão download
        WebDriverWait(driver, 600).until(
            EC.presence_of_element_located((By.XPATH, "//a[contains(.,'Download')]"))
        ).click()

        # aguarda zip aparecer
        for _ in range(300):
            zips = list(ZIP_DIR.glob("*.zip"))
            if zips:
                return zips[0]
            time.sleep(2)
        return None
    finally:
        try:
            driver.quit()
        except Exception:
            pass

@app.route("/ideogram", methods=["POST"])
def ideogram():
    data = request.get_json(force=True, silent=True) or {}
    prompts = data.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        return jsonify({"error": "campo 'prompts' deve ser lista"}), 400

    tmp_csv = Path(tempfile.gettempdir()) / f"{uuid.uuid4()}.csv"
    _create_batch_csv(prompts, tmp_csv)

    try:
        zip_path = _upload_and_download(tmp_csv)
        if not zip_path:
            return jsonify({"error": "timeout ao gerar imagens"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        tmp_csv.unlink(missing_ok=True)

    static_dir = Path("static")
    static_dir.mkdir(exist_ok=True)
    dest = static_dir / zip_path.name
    zip_path.replace(dest)
    zip_url = request.url_root.rstrip("/") + "/static/" + dest.name
    return jsonify({"zip_url": zip_url})

# ----------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))
