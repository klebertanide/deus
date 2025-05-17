import os
import io
import csv
import re
import zipfile
import tempfile
import requests
import unidecode
from pathlib import Path
from flask import Flask, request, jsonify
import openai
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# —————— Configuração Google Drive ——————
GOOGLE_DRIVE_ROOT_FOLDER = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
SERVICE_ACCOUNT_FILE     = "/etc/secrets/service_account.json"
ELEVEN_API_KEY           = os.getenv("ELEVENLABS_API_KEY")
openai.api_key           = os.getenv("OPENAI_API_KEY")

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_pasta_drive(nome, drive):
    meta = {
        "name": nome,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [GOOGLE_DRIVE_ROOT_FOLDER]
    }
    fld = drive.files().create(body=meta, fields="id").execute()
    return fld["id"]

def upload_para_drive(path: Path, nome: str, folder_id: str, drive):
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(
        body={"name": nome, "parents":[folder_id]},
        media_body=media
    ).execute()

# —————— Helpers ——————
def slugify(text: str, limit: int = 30) -> str:
    txt = unidecode.unidecode(text)
    txt = re.sub(r"[^\w\s]", "", txt)
    return txt.strip().replace(" ", "_").lower()[:limit]

def elevenlabs_tts(text: str) -> bytes:
    headers = {
        "xi-api-key": ELEVEN_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "text": text,
        "voice_settings": {
            "stability": 0.6,
            "similarity_boost": 0.9,
            "style": 0.15,
            "use_speaker_boost": True
        },
        "model_id": "eleven_multilingual_v2",
        "voice_id":  "cwIsrQsWEVTols6slKYN"
    }
    r = requests.post(
        "https://api.elevenlabs.io/v1/text-to-speech/cwIsrQsWEVTols6slKYN",
        headers=headers,
        json=payload
    )
    r.raise_for_status()
    return r.content

def parse_ts(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms      = rest.split(",")
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

# —————— Rotas ——————
@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

@app.route("/falar", methods=["POST"])
def falar():
    data  = request.get_json(force=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug     = slugify(texto)
    mp3_path = Path(f"{slug}.mp3")

    try:
        audio_bytes = elevenlabs_tts(texto)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    mp3_path.write_bytes(audio_bytes)
    return jsonify(
        audio_url=str(mp3_path.resolve()),
        slug=slug
    )

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json(force=True) or {}
    audio_ref = data.get("audio_url") or data.get("audio_file")
    if not audio_ref:
        return jsonify(error="campo 'audio_url' ou 'audio_file' obrigatório"), 400

    try:
        if os.path.exists(audio_ref):
            fobj = open(audio_ref, "rb")
        else:
            resp = requests.get(audio_ref, timeout=60)
            resp.raise_for_status()
            fobj = io.BytesIO(resp.content)
            fobj.name = Path(audio_ref).name
    except Exception as e:
        return jsonify(error="falha ao carregar áudio", detalhe=str(e)), 400

    try:
        # transcrição em SRT
        srt = openai.audio.transcriptions.create(
            model="whisper-1",
            file=fobj,
            response_format="srt"
        )
        segmentos = []
        # quebrar a cada ~3-4 palavras
        for bloco in srt.strip().split("\n\n"):
            lines = bloco.split("\n")
            if len(lines) < 3: 
                continue
            st, en  = lines[1].split(" --> ")
            txt_full = " ".join(lines[2:])
            words = txt_full.split()
            # refaz blocos a cada 4 palavras
            for i in range(0, len(words), 4):
                part = " ".join(words[i:i+4])
                t0 = parse_ts(st) + (i/len(words)) * (parse_ts(en)-parse_ts(st))
                t1 = min(parse_ts(en), t0 + 2.5)
                segmentos.append({
                    "inicio": round(t0,3),
                    "fim":    round(t1,3),
                    "texto":  part
                })

        total = segmentos[-1]["fim"] if segmentos else 0
        return jsonify(transcricao=segmentos, duracao_total=total)

    except Exception as e:
        return jsonify(error="falha na transcrição", detalhe=str(e)), 500

    finally:
        try: fobj.close()
        except: pass

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data        = request.get_json() or {}
    transcricao = data.get("transcricao", [])
    prompts     = data.get("prompts", [])
    texto_orig  = data.get("texto_original", "")
    if not transcricao or not prompts or len(transcricao) != len(prompts):
        return jsonify(error="transcricao+prompts inválidos"), 400

    slug      = slugify(texto_orig)
    drive     = get_drive_service()
    folder_id = criar_pasta_drive(slug, drive)

    # — Gera descrição (.txt) —
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role":"system", "content":
                 "Você é um assistente que cria descrições poéticas e motivacionais para redes sociais."},
                {"role":"user", "content":
                 f"Escreva 2–3 frases inspiradoras com 'Siga @DeusTeEnviouIsso' de forma natural e finalize com 5 hashtags, baseado neste texto:\n\n{texto_orig}"}
            ],
            temperature=0.7
        )
        descricao = resp.choices[0].message.content.strip()
    except:
        descricao = ""

    txt_path = Path(f"{slug}.txt")
    if descricao:
        txt_path.write_text(descricao, encoding="utf-8")
        upload_para_drive(txt_path, txt_path.name, folder_id, drive)

    # — Gera CSV (.csv) —
    csv_path = Path(f"{slug}.csv")
    neg = ("low quality, overexposed, underexposed, extra limbs, "
           "missing fingers, bad anatomy, realistic style, photographic style, text")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "TIME","PROMPT","VISIBILITY","ASPECT_RATIO",
            "MAGIC_PROMPT","MODEL","SEED_NUMBER","RENDERING",
            "NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"
        ])
        for seg, p in zip(transcricao, prompts):
            t = int(seg["inicio"])
            w.writerow([
                t,
                p,
                "PRIVATE","9:16","ON","3.0","","TURBO",
                neg,"AUTO",""
            ])
    upload_para_drive(csv_path, csv_path.name, folder_id, drive)

    # — Gera SRT (.srt) —
    def fmt(s):
        ms = int((s%1)*1000); h=int(s//3600)
        m = int((s%3600)//60); sec=int(s%60)
        return f"{h:02}:{m:02}:{sec:02},{ms:03}"
    srt_path = Path(f"{slug}.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(transcricao, 1):
            f.write(f"{i}\n{fmt(seg['inicio'])} --> {fmt(seg['fim'])}\n{seg['texto']}\n\n")
    upload_para_drive(srt_path, srt_path.name, folder_id, drive)

    # — Reenvia o MP3 —
    mp3 = Path(f"{slug}.mp3")
    if mp3.exists():
        upload_para_drive(mp3, mp3.name, folder_id, drive)

    return jsonify(
        slug=slug,
        folder_url=f"https://drive.google.com/drive/folders/{folder_id}"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0",
            port=int(os.getenv("PORT", "5000")),
            debug=True)
