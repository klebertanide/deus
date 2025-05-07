import os, uuid, io
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv
import requests, csv

load_dotenv()
app = Flask(__name__)

AUDIO_DIR = Path("audio"); AUDIO_DIR.mkdir(exist_ok=True)
CSV_DIR = Path("csv"); CSV_DIR.mkdir(exist_ok=True)

ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

@app.route("/upload_audio", methods=["POST"])
def upload_audio():
    if "file" not in request.files:
        return jsonify({"error": "campo 'file' com .mp3 obrigatório"}), 400
    f = request.files["file"]
    ext = Path(f.filename).suffix
    if ext.lower() != ".mp3":
        return jsonify({"error": "arquivo deve ser .mp3"}), 400
    fname = f"{uuid.uuid4()}.mp3"
    path = AUDIO_DIR / fname
    f.save(path)
    audio_url = request.url_root.rstrip('/') + "/audio/" + fname
    return jsonify({"audio_url": audio_url})

@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json(force=True, silent=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify({"error": "campo 'texto' obrigatório"}), 400
    url = f"https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN/stream"
    headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
    payload = {
        "text": texto,
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
    filename = f"{uuid.uuid4()}.mp3"
    path = AUDIO_DIR / filename
    with open(path, "wb") as f:
        f.write(r.content)
    audio_url = request.url_root.rstrip('/') + "/audio/" + filename
    return jsonify({"audio_url": audio_url})

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True, silent=True) or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error": "campo 'audio_url' obrigatório"}), 400
    try:
        r = requests.get(audio_url)
        r.raise_for_status()
        buf = io.BytesIO(r.content)
        buf.name = "audio.mp3"
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=buf,
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

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json(force=True, silent=True) or {}
    transcricao = data.get("transcricao")
    if not transcricao:
        return jsonify({"error": "campo 'transcricao' obrigatório"}), 400
    fname = f"{uuid.uuid4()}.csv"
    path = CSV_DIR / fname
    with open(path, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        for i, bloco in enumerate(transcricao, 1):
            inicio = round(bloco.get("inicio", 0))
            writer.writerow([i, inicio])
    csv_url = request.url_root.rstrip('/') + "/csv/" + fname
    return jsonify({"csv_url": csv_url})

@app.route("/audio/<path:filename>")
def serve_audio(filename):
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg")

@app.route("/csv/<path:filename>")
def serve_csv(filename):
    return send_from_directory(CSV_DIR, filename, mimetype="text/csv")

if __name__ == "__main__":
    app.run(debug=True)
