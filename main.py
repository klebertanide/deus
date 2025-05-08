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
TXT_DIR = BASE / "txt"
SRT_DIR = BASE / "srt"
ZIP_DIR = BASE / "zip"
for d in [AUDIO_DIR, CSV_DIR, TXT_DIR, SRT_DIR, ZIP_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Chaves
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_API_KEY")
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# ===== Função ElevenLabs =====
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

# ===== Falar + Transcrever + CSV + SRT + ZIP =====
@app.route("/processar", methods=["POST"])
def processar():
    data = request.get_json(force=True, silent=True) or {}
    texto = data.get("texto")
    prompts = data.get("prompts")
    descricao = data.get("descricao")

    if not texto or not prompts or not descricao:
        return jsonify({"error": "Campos 'texto', 'prompts' e 'descricao' são obrigatórios."}), 400

    try:
        uid = str(uuid.uuid4())
        filename_base = f"brilho_{uid}"

        # ===== Gerar áudio =====
        audio_bytes = elevenlabs_tts(texto)
        mp3_path = AUDIO_DIR / f"{filename_base}.mp3"
        with open(mp3_path, "wb") as f:
            f.write(audio_bytes)

        # ===== Transcrever =====
        audio_file = open(mp3_path, "rb")
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=audio_file,
            response_format="verbose_json",
            temperature=0,
            timestamp_granularities=["segment"]
        )
        segments = transcript.segments

        # ===== SRT =====
        srt_path = SRT_DIR / f"{filename_base}.srt"
        with open(srt_path, "w", encoding="utf-8") as srt:
            for i, seg in enumerate(segments, 1):
                ini = format_ts(seg.start)
                fim = format_ts(seg.end)
                text = seg.text.strip()
                srt.write(f"{i}\n{ini} --> {fim}\n{text}\n\n")

        # ===== CSV =====
        if len(prompts) != len(segments):
            return jsonify({"error": "A quantidade de prompts deve ser igual à de segmentos do áudio."}), 400

        csv_path = CSV_DIR / f"{filename_base}.csv"
        with open(csv_path, "w", newline='', encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "PROMPT", "VISIBILITY", "ASPECT_RATIO", "MAGIC_PROMPT", "MODEL",
                "SEED_NUMBER", "RENDERING", "NEGATIVE_PROMPT", "STYLE", "COLOR_PALETTE"
            ])
            for seg, prompt in zip(segments, prompts):
                segundo = int(round(seg.start))
                prompt_final = f'{segundo} - Painting style: Traditional Japanese oriental watercolor, with soft brush strokes and handmade paper texture. {prompt}'
                if "," in prompt_final:
                    prompt_final = f'"{prompt_final}"'
                writer.writerow([
                    prompt_final, "PRIVATE", "9:16", "ON", "3.0", "", "TURBO",
                    "low quality, overexposed, underexposed, extra limbs, extra fingers, missing fingers, disfigured, deformed, bad anatomy, crooked eyes, mutated hands",
                    "AUTO", ""
                ])

        # ===== TXT (descrição) =====
        txt_path = TXT_DIR / f"{filename_base}.txt"
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(descricao.strip())

        # ===== ZIP =====
        zip_path = ZIP_DIR / f"{filename_base}.zip"
        with zipfile.ZipFile(zip_path, "w") as z:
            z.write(mp3_path, arcname="voz.mp3")
            z.write(csv_path, arcname="imagens.csv")
            z.write(srt_path, arcname="legenda.srt")
            z.write(txt_path, arcname="descricao.txt")

        base = request.url_root.rstrip("/")
        return jsonify({
            "voz_mp3": f"{base}/audio/{mp3_path.name}",
            "imagens_csv": f"{base}/csv/{csv_path.name}",
            "legenda_srt": f"{base}/srt/{srt_path.name}",
            "descricao_txt": f"{base}/txt/{txt_path.name}",
            "pacote_zip": f"{base}/zip/{zip_path.name}"
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        try:
            audio_file.close()
        except:
            pass

# ===== Utilitário para SRT =====
def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

# ===== Servir arquivos =====
@app.route("/audio/<path:filename>")
def download_audio(filename):
    return send_from_directory(AUDIO_DIR, filename)

@app.route("/csv/<path:filename>")
def download_csv(filename):
    return send_from_directory(CSV_DIR, filename)

@app.route("/srt/<path:filename>")
def download_srt(filename):
    return send_from_directory(SRT_DIR, filename)

@app.route("/txt/<path:filename>")
def download_txt(filename):
    return send_from_directory(TXT_DIR, filename)

@app.route("/zip/<path:filename>")
def download_zip(filename):
    return send_from_directory(ZIP_DIR, filename)

# ===== Local run =====
if __name__ == "__main__":
    app.run(debug=True)
