from flask import Flask, request, jsonify, send_from_directory
import os
import requests
import uuid
import whisper
from pydub.utils import mediainfo

app = Flask(__name__)

PASTA_AUDIOS = "audios"
os.makedirs(PASTA_AUDIOS, exist_ok=True)

API_KEY_ELEVENLABS = os.environ.get("ELEVEN_API_KEY")
VOICE_ID = "cwIsrQsWEVTols6slKYN"
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")

@app.route("/falar", methods=["POST"])
def falar():
    try:
        data = request.json
        texto = data.get("texto")

        if not texto:
            return jsonify({"erro": "Campo 'texto' ausente"}), 400

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE_ID}"
        payload = {
            "text": texto,
            "model_id": "eleven_multilingual_v2",
            "voice_settings": {
                "stability": 0.60,
                "similarity_boost": 0.90,
                "style": 0.15,
                "use_speaker_boost": True,
                "speed": 0.99
            }
        }
        headers = {
            "xi-api-key": API_KEY_ELEVENLABS,
            "Content-Type": "application/json"
        }
        resposta = requests.post(url, headers=headers, json=payload)

        if resposta.status_code != 200:
            return jsonify({"erro": "Erro ao gerar áudio", "detalhe": resposta.text}), 500

        nome_arquivo = f"{uuid.uuid4()}.mp3"
        caminho = os.path.join(PASTA_AUDIOS, nome_arquivo)
        with open(caminho, "wb") as f:
            f.write(resposta.content)

        dominio = request.host_url.rstrip("/")
        return jsonify({"audio_url": f"{dominio}/audios/{nome_arquivo}"})
    except Exception as e:
        return jsonify({"erro": "Erro interno", "detalhe": str(e)}), 500

@app.route("/transcrever", methods=["POST"])
def transcrever():
    try:
        data = request.json
        url_audio = data.get("url")

        if not url_audio:
            return jsonify({"erro": "Campo 'url' ausente"}), 400

        resposta = requests.get(url_audio)
        if resposta.status_code != 200:
            return jsonify({"erro": "Erro ao baixar o áudio"}), 400

        nome_local = f"{uuid.uuid4()}.mp3"
        caminho_local = os.path.join(PASTA_AUDIOS, nome_local)
        with open(caminho_local, "wb") as f:
            f.write(resposta.content)

        modelo = whisper.load_model("base")
        resultado = modelo.transcribe(caminho_local, verbose=False, word_timestamps=True)

        retorno = []
        for segmento in resultado["segments"]:
            retorno.append({
                "inicio": round(segmento["start"], 2),
                "fim": round(segmento["end"], 2),
                "texto": segmento["text"].strip()
            })

        return jsonify({"transcricao": retorno})
    except Exception as e:
        return jsonify({"erro": "Erro interno", "detalhe": str(e)}), 500

@app.route("/audios/<path:nome>")
def servir_audio(nome):
    return send_from_directory(PASTA_AUDIOS, nome)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3000)