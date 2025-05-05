
import os, uuid, io
import requests
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI

app = Flask(__name__)
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
    """Returns a file-like obj for Whisper."""
    if audio_url.startswith(request.url_root.rstrip('/')):
        fname = audio_url.split('/audio/')[-1]
        p = AUDIO_DIR / fname
        if p.exists():
            return open(p, 'rb')
    # fallback to remote fetch
    resp = requests.get(audio_url, timeout=60)
    resp.raise_for_status()
    file_obj = io.BytesIO(resp.content)
    file_obj.name = "remote.mp3"
    return file_obj

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
        segments = [
            {"inicio": seg["start"], "fim": seg["end"], "texto": seg["text"]}
            for seg in transcript["segments"]
        ]
        return jsonify({"duracao_total": transcript.get("duration"), "transcricao": segments})
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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 3000)))
