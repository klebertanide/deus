import os, io, re, csv, zipfile, requests, unidecode, time, shutil
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import openai
from moviepy.editor import *
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from sentence_transformers import SentenceTransformer, util

app = Flask(__name__)

BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR = BASE / "csv"
FILES_DIR = BASE / "downloads"
IMGS_DIR = FILES_DIR / "imgs"
for d in [AUDIO_DIR, CSV_DIR, FILES_DIR, IMGS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_KEY

model_clip = SentenceTransformer("clip-ViT-B-32")

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(texto, limite=30):
    texto = unidecode.unidecode(texto)
    texto = re.sub(r"[^\w\s]", "", texto)
    return texto.strip().replace(" ", "_")[:limite].lower()

@app.route("/falar", methods=["POST"])
def falar():
    texto = request.json.get("texto")
    if not texto: return jsonify({"error": "campo texto obrigatório"}), 400
    slug = slugify(texto)
    filename = f"{slug}.mp3"
    audio_path = AUDIO_DIR / filename

    audio = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
        headers={"xi-api-key": ELEVEN_API_KEY},
        json={"text": texto}
    ).content

    audio_path.write_bytes(audio)
    return jsonify({"audio_url": f"{request.url_root.rstrip('/')}/audio/{filename}", "slug": slug})

@app.route("/audio/<fn>")
def serve_audio(fn): return send_from_directory(AUDIO_DIR, fn)

@app.route("/transcrever", methods=["POST"])
def transcrever():
    audio_url = request.json.get("audio_url")
    if not audio_url:
    return jsonify({"error": "audio_url não fornecida ou inválida"}), 400

try:
    audio_bytes = requests.get(audio_url).content
except requests.exceptions.MissingSchema:
    return jsonify({"error": "audio_url inválida, verifique a URL fornecida"}), 400
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "audio.mp3"

    srt = openai.Audio.transcribe("whisper-1", audio_file, response_format="srt")

    def parse_ts(ts):
        h, m, rest = ts.split(":"); s, ms = rest.split(",")
        return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

    segments = []
    for block in srt.strip().split("\n\n"):
        idx, times, text = block.split("\n", 2)
        start, end = times.split(" --> ")
        segments.append({"inicio": parse_ts(start), "fim": parse_ts(end), "texto": text})

    return jsonify({"duracao_total": segments[-1]["fim"], "transcricao": segments})

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    transcricao = request.json.get("transcricao", [])
    prompts = request.json.get("prompts", [])
    descricao = request.json.get("descricao", "")
    slug = slugify(descricao or prompts[0])

    if len(transcricao) != len(prompts):
        return jsonify({"error": "transcricao e prompts inválidos"}), 400

    drive = get_drive_service()
    pasta_id = drive.files().create(body={
        "name": slug, "mimeType": "application/vnd.google-apps.folder", "parents": [GOOGLE_DRIVE_FOLDER_ID]
    }).execute()["id"]

    csv_path = CSV_DIR / f"{slug}.csv"
    srt_path = FILES_DIR / f"{slug}.srt"
    txt_path = FILES_DIR / f"{slug}.txt"
    mp3_path = AUDIO_DIR / f"{slug}.mp3"

    with open(csv_path, "w", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["PROMPT", "VISIBILITY", "ASPECT_RATIO", "MAGIC_PROMPT"])
        for seg, prompt in zip(transcricao, prompts):
            w.writerow([f"{int(seg['inicio'])} {prompt}", "PRIVATE", "9:16", "ON"])

    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(transcricao, 1):
            f.write(f"{i}\n{seg['inicio']} --> {seg['fim']}\n{seg['texto']}\n\n")

    txt_path.write_text(descricao.strip(), encoding="utf-8")

    for path, name in [(csv_path, "imagens.csv"), (srt_path, "legenda.srt"), (txt_path, "descricao.txt"), (mp3_path, "voz.mp3")]:
        drive.files().create(body={"name": name, "parents": [pasta_id]}, media_body=MediaFileUpload(path)).execute()

    return jsonify({"folder_url": f"https://drive.google.com/drive/folders/{pasta_id}"})

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    zip_file = request.files.get("zip")
    if not zip_file:
        return jsonify({"error": "Envie o arquivo ZIP"}), 400

    temp_dir = FILES_DIR / "temp_imgs"
    if temp_dir.exists(): shutil.rmtree(temp_dir)
    temp_dir.mkdir(exist_ok=True)
    zip_path = temp_dir / "imagens.zip"
    zip_file.save(zip_path)

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(temp_dir)

    prompts = []
    slug = next(CSV_DIR.glob("*.csv")).stem
    with open(CSV_DIR / f"{slug}.csv", encoding="utf-8") as f:
        prompts = [row[0] for row in csv.reader(f)][1:]

    imagens_zip = list(temp_dir.glob("*.png"))
    selecionadas = []

    for prompt in prompts:
        emb_prompt = model_clip.encode(prompt, convert_to_tensor=True)
        melhor = max(imagens_zip, key=lambda img: util.cos_sim(emb_prompt, model_clip.encode(img.stem, convert_to_tensor=True)))
        selecionadas.append(melhor)
        imagens_zip.remove(melhor)
        shutil.copy(melhor, IMGS_DIR)

    return jsonify({"status": f"{len(selecionadas)} imagens selecionadas e salvas"})

@app.route("/montar_video", methods=["POST"])
def montar_video():
    slug = next(CSV_DIR.glob("*.csv")).stem
    audio_clip = AudioFileClip(str(AUDIO_DIR / f"{slug}.mp3"))

    prompts = []
    with open(CSV_DIR / f"{slug}.csv", encoding="utf-8") as f:
        prompts = [row[0].split(" ", 1) for row in csv.reader(f)][1:]

    clips = []
    for sec, _ in prompts:
        img_file = sorted(IMGS_DIR.glob("*.png"))[0]  # Simplificado
        clip = ImageClip(str(img_file)).set_duration(4).resize((720,1280))
        clips.append(clip)

    video = concatenate_videoclips(clips).set_audio(audio_clip)
    final_path = FILES_DIR / f"{slug}.mp4"
    video.write_videofile(str(final_path), fps=24)

    drive = get_drive_service()
    pasta = drive.files().list(q=f"name='{slug}' and '{GOOGLE_DRIVE_FOLDER_ID}' in parents").execute()["files"][0]["id"]
    drive.files().create(body={"name": "video_final.mp4", "parents": [pasta]}, media_body=MediaFileUpload(final_path)).execute()

    return jsonify({"video": f"https://drive.google.com/drive/folders/{pasta}"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 5000)), debug=False)