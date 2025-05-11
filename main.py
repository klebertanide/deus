import os, uuid, io, csv, zipfile
import requests
from flask import Flask, request, jsonify, send_from_directory
from pathlib import Path
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# Pastas
BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR = BASE / "csv"
FILES_DIR = BASE / "downloads"
for d in [AUDIO_DIR, CSV_DIR, FILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Chaves
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_KEY)

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

# ===== Utilitário para legenda SRT =====
def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

# ===== Rotas principais =====
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


@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json(force=True, silent=True) or {}
    audio_url = data.get("audio_url")
    if not audio_url:
        return jsonify({"error": "campo 'audio_url' obrigatório"}), 400

    try:
        if audio_url.startswith(request.url_root.rstrip('/')):
            fname = audio_url.split('/audio/')[-1]
            p = AUDIO_DIR / fname
            if not p.exists():
                raise Exception("Arquivo local não encontrado.")
            audio_file = open(p, 'rb')
        else:
            resp = requests.get(audio_url, timeout=60)
            resp.raise_for_status()
            audio_file = io.BytesIO(resp.content)
            audio_file.name = "remote.mp3"

        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            timestamp_granularities=["segment"]
        )

        duration = transcript.duration
        segments = [{"inicio": s.start, "fim": s.end, "texto": s.text} for s in transcript.segments]
        return jsonify({"duracao_total": duration, "transcricao": segments})

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            audio_file.close()
        except:
            pass


@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json(force=True, silent=True) or {}
    transcricao = data.get("transcricao")
    prompts = data.get("prompts", [])
    descricao = data.get("descricao", "Descrição não fornecida")

    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify({"error": "É necessário fornecer listas 'transcricao' e 'prompts' com o mesmo tamanho."}), 400

    uid = str(uuid.uuid4())
    base_name = f"deus_{uid}"

    # CSV
    csv_path = CSV_DIR / f"{base_name}.csv"
    header = [
        "PROMPT", "VISIBILITY", "ASPECT_RATIO", "MAGIC_PROMPT", "MODEL",
        "SEED_NUMBER", "RENDERING", "NEGATIVE_PROMPT", "STYLE", "COLOR_PALETTE"
    ]
    negative_prompt = "low quality, overexposed, underexposed, extra limbs, extra fingers, missing fingers, disfigured, deformed, bad anatomy, crooked eyes, mutated hands"

    with open(csv_path, "w", newline='', encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for bloco, prompt in zip(transcricao, prompts):
            segundo = int(round(bloco.get("inicio", 0)))
            prompt_final = f'{segundo} - Painting style: Traditional watercolor, with soft brush strokes and handmade paper texture. {prompt}'
            if "," in prompt_final:
                prompt_final = f'"{prompt_final}"'
            writer.writerow([
                prompt_final, "PRIVATE", "9:16", "ON", "3.0", "", "TURBO",
                negative_prompt, "AUTO", ""
            ])

    # SRT
    srt_path = FILES_DIR / f"{base_name}.srt"
    with open(srt_path, "w", encoding="utf-8") as srt:
        for i, seg in enumerate(transcricao, 1):
            ini = format_ts(seg["inicio"])
            fim = format_ts(seg["fim"])
            text = seg["texto"].strip()
            srt.write(f"{i}\n{ini} --> {fim}\n{text}\n\n")

    # TXT
    txt_path = FILES_DIR / f"{base_name}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(descricao.strip())

    # ZIP
    zip_path = FILES_DIR / f"{base_name}.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.write(csv_path, arcname="imagens.csv")
        z.write(srt_path, arcname="legenda.srt")
        z.write(txt_path, arcname="descricao.txt")

    base_url = request.url_root.rstrip("/")
    return jsonify({
        "csv_url": f"{base_url}/csv/{csv_path.name}",
        "srt_url": f"{base_url}/downloads/{srt_path.name}",
        "txt_url": f"{base_url}/downloads/{txt_path.name}",
        "zip_url": f"{base_url}/downloads/{zip_path.name}"
    })


# ===== Arquivos públicos =====
@app.route("/audio/<path:filename>")
def baixar_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

@app.route("/csv/<path:filename>")
def baixar_csv(filename):
    return send_from_directory(CSV_DIR, filename)

@app.route("/downloads/<path:filename>")
def baixar_download(filename):
    return send_from_directory(FILES_DIR, filename)

if __name__ == "__main__":
    app.run(debug=True)
