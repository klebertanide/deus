import os, uuid, io, csv
import requests
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

AUDIO_DIR = Path("audio")
CSV_DIR = Path("csv")
SRT_DIR = Path("srt")
for folder in [AUDIO_DIR, CSV_DIR, SRT_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ===== /falar =====
def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN"):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {"xi-api-key": os.getenv("ELEVENLABS_API_KEY"), "Content-Type": "application/json"}
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
    return jsonify({"audio_url": request.url_root.rstrip("/") + f"/audio/{filename}"})


# ===== /transcrever (upload direto) =====
@app.route("/transcrever", methods=["POST"])
def transcrever():
    if "file" not in request.files:
        return jsonify({"error": "campo 'file' (multipart/form-data) obrigatório"}), 400

    audio_file = request.files["file"]
    if not audio_file.filename.endswith(".mp3"):
        return jsonify({"error": "O arquivo deve ser .mp3"}), 400

    try:
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

# ===== /gerar_csv =====
@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json(force=True, silent=True) or {}
    transcricao = data.get("transcricao", [])
    if not transcricao:
        return jsonify({"error": "lista 'transcricao' obrigatória"}), 400

    filename = f"{uuid.uuid4()}.csv"
    path = CSV_DIR / filename
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["IMG", "TEMPO_SEG"])
        for i, bloco in enumerate(transcricao):
            tempo = round(bloco["inicio"])
            writer.writerow([i + 1, tempo])

    return jsonify({"csv_url": request.url_root.rstrip("/") + f"/csv/{filename}"})


# ===== /gerar_srt =====
@app.route("/gerar_srt", methods=["POST"])
def gerar_srt():
    data = request.get_json(force=True, silent=True) or {}
    transcricao = data.get("transcricao", [])
    if not transcricao:
        return jsonify({"error": "lista 'transcricao' obrigatória"}), 400

    filename = f"{uuid.uuid4()}.srt"
    path = SRT_DIR / filename

    def format_timestamp(seconds):
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds - int(seconds)) * 1000)
        return f"{h:02}:{m:02}:{s:02},{ms:03}"

    with open(path, "w", encoding="utf-8") as f:
        count = 1
        for seg in transcricao:
            palavras = seg["texto"].split()
            for i in range(0, len(palavras), 4):
                trecho = " ".join(palavras[i:i+4])
                inicio = seg["inicio"] + (i / len(palavras)) * (seg["fim"] - seg["inicio"])
                fim = seg["inicio"] + ((i + 4) / len(palavras)) * (seg["fim"] - seg["inicio"])
                f.write(f"{count}\n")
                f.write(f"{format_timestamp(inicio)} --> {format_timestamp(min(fim, seg['fim']))}\n")
                f.write(f"{trecho.strip()}\n\n")
                count += 1

    return jsonify({"srt_url": request.url_root.rstrip("/") + f"/srt/{filename}"})


# ===== Servir arquivos =====
@app.route("/audio/<path:filename>")
def servir_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

@app.route("/csv/<path:filename>")
def servir_csv(filename):
    return send_from_directory(CSV_DIR, filename)

@app.route("/srt/<path:filename>")
def servir_srt(filename):
    return send_from_directory(SRT_DIR, filename)

if __name__ == "__main__":
    app.run(debug=True)
