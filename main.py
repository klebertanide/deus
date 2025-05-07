import os, uuid, io, csv
import requests
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# Pastas de saída
AUDIO_DIR = Path("audio")
CSV_DIR = Path("csv")
AUDIO_DIR.mkdir(exist_ok=True)
CSV_DIR.mkdir(exist_ok=True)

# Chaves
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ===== /falar =====
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

# ===== /upload_audio =====
@app.route("/upload_audio", methods=["POST"])
def upload_audio():
    if "file" not in request.files:
        return jsonify({"error": "campo 'file' obrigatório"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "arquivo sem nome"}), 400

    filename = f"{uuid.uuid4()}.mp3"
    path = AUDIO_DIR / filename
    file.save(path)

    audio_url = request.url_root.rstrip('/') + '/audio/' + filename
    return jsonify({"audio_url": audio_url})

# ===== /transcrever =====
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
        blocos = [
            {"inicio": seg.start, "fim": seg.end, "texto": seg.text}
            for seg in transcript.segments
        ]
        return jsonify({"duracao_total": duration, "transcricao": blocos})
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
    transcricao = data.get("transcricao", [])
    if not transcricao:
        return jsonify({"error": "campo 'transcricao' obrigatório"}), 400

    filename = f"{uuid.uuid4()}.csv"
    path = CSV_DIR / filename

    with open(path, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["imagem", "segundo"])
        for i, bloco in enumerate(transcricao):
            segundo = round(bloco.get("inicio", 0))
            writer.writerow([i + 1, segundo])

    csv_url = request.url_root.rstrip('/') + '/csv/' + filename
    return jsonify({"csv_url": csv_url})

# ===== Servir arquivos =====
@app.route("/audio/<path:filename>")
def baixar_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

@app.route("/csv/<path:filename>")
def baixar_csv(filename):
    return send_from_directory(CSV_DIR, filename)

# ===== Execução local opcional =====
if __name__ == "__main__":
    app.run(debug=True)
