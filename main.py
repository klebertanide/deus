
import os
import uuid
import io
import requests
from flask import Flask, request, jsonify, send_from_directory
import openai
from pathlib import Path

app = Flask(__name__)

# Diretório onde os MP3 ficam salvos
AUDIO_DIR = Path(os.getenv("AUDIO_DIR", "audio"))
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# Chaves de API (Render → Environment Vars)
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_API_KEY

DEFAULT_VOICE_ID = "cwIsrQsWEVTols6slKYN"

def elevenlabs_tts(texto: str, voice_id: str = DEFAULT_VOICE_ID, model: str = "eleven_multilingual_v2") -> bytes:
    """Gera áudio TTS na ElevenLabs e devolve bytes do MP3"""
    if not ELEVEN_API_KEY:
        raise RuntimeError("ELEVENLABS_API_KEY não definida")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": texto,
        "model_id": model,
        "voice_settings": {
            "stability": 0.60,
            "similarity_boost": 0.90,
            "style": 0.15,
            "use_speaker_boost": True
        }
    }
    resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
    resp.raise_for_status()
    return resp.content

@app.route("/falar", methods=["POST"])
def falar():
    """Recebe JSON {texto, voice_id?} e devolve {audio_url} com o MP3 gerado"""
    data = request.get_json(silent=True) or {}
    texto = data.get("texto") or data.get("text")
    if not texto:
        return jsonify({"erro": "Campo 'texto' obrigatório"}), 400
    voice_id = data.get("voice_id", DEFAULT_VOICE_ID)

    try:
        audio_bytes = elevenlabs_tts(texto, voice_id=voice_id)
    except Exception as e:
        return jsonify({"erro": str(e)}), 500

    filename = f"{uuid.uuid4()}.mp3"
    filepath = AUDIO_DIR / filename
    filepath.write_bytes(audio_bytes)

    audio_url = request.url_root.rstrip('/') + '/audio/' + filename
    return jsonify({"audio_url": audio_url})

@app.route("/transcrever", methods=["POST"])
def transcrever():
    """Recebe JSON {audio_url} e devolve minutagem + texto usando Whisper‑1"""
    data = request.get_json(silent=True) or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"erro": "Campo 'audio_url' obrigatório"}), 400

    # Obtém o arquivo (local ou remoto)
    try:
        if audio_url.startswith(request.url_root.rstrip('/')):
            # arquivo local
            filename = audio_url.split("/audio/")[-1]
            audio_file = open(AUDIO_DIR / filename, "rb")
        else:
            r = requests.get(audio_url, timeout=60)
            r.raise_for_status()
            audio_file = io.BytesIO(r.content)
            audio_file.name = "remote.mp3"
    except Exception as e:
        return jsonify({"erro": f"Falha ao baixar áudio: {e}"}), 500

    # Chamada Whisper
    try:
        resposta = openai.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )
    except Exception as e:
        return jsonify({"erro": str(e)}), 500
    finally:
        audio_file.close()

    # Converte para formato simplificado
    segmentos = resposta.segments  # list of dicts
    retorno = []
    for s in segmentos:
        retorno.append({
            "inicio": round(s["start"], 2),
            "fim": round(s["end"], 2),
            "texto": s["text"].strip()
        })
    duracao_total = round(retorno[-1]["fim"], 2) if retorno else 0.0

    return jsonify({
        "duracao_total": duracao_total,
        "transcricao": retorno
    })

@app.route("/audio/<path:filename>")
def servir_audio(filename):
    """Serve o MP3 gravado em /audio"""
    return send_from_directory(AUDIO_DIR, filename, mimetype="audio/mpeg")

if __name__ == "__main__":
    port = int(os.getenv("PORT", 3000))
    app.run(host="0.0.0.0", port=port)
