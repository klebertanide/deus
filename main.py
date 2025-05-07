import os, uuid, io, tempfile, requests, csv
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

AUDIO_DIR = Path("audio")
CSV_DIR = Path("csv")
AUDIO_DIR.mkdir(parents=True, exist_ok=True)
CSV_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")

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

    # Envia para transfer.sh
    with open(path, "rb") as f:
        r = requests.post(f"https://transfer.sh/{filename}", files={"file": f})
        r.raise_for_status()
        audio_url = r.text.strip()

    return jsonify({"audio_url": audio_url})

# ===== /transcrever =====
@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True, silent=True) or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error": "campo 'audio_url' obrigatório"}), 400
    try:
        audio_file = requests.get(audio_url, timeout=60)
        audio_file.raise_for_status()
        buf = io.BytesIO(audio_file.content)
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

# ===== /gerar_csv =====
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json(force=True, silent=True) or {}
    transcricao = data.get("transcricao", [])

    if not transcricao:
        return jsonify({"error": "lista 'transcricao' obrigatória"}), 400

    filename = f"{uuid.uuid4()}.csv"
    path = CSV_DIR / filename

    with open(path, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["imagem", "tempo_segundos"])
        for idx, bloco in enumerate(transcricao, start=1):
            tempo = int(bloco["inicio"])
            writer.writerow([idx, tempo])

    csv_url = request.url_root.rstrip('/') + '/csv/' + filename
    return jsonify({"csv_url": csv_url})

# ===== Servir arquivos =====
@app.route("/csv/<path:filename>")
def baixar_csv(filename):
    return send_from_directory(CSV_DIR, filename)

@app.route("/audio/<path:filename>")
def baixar_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

# ===== Run local =====
if __name__ == "__main__":
    app.run(debug=True)
