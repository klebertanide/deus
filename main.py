import os
import io
import csv
import re
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

def fmt_ts(seconds: float) -> str:
    ms = int((seconds % 1) * 1000)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

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
    return jsonify(audio_url=str(mp3_path.resolve()), slug=slug)

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data      = request.get_json(force=True) or {}
    audio_ref = data.get("audio_url")
    if not audio_ref:
        return jsonify(error="campo 'audio_url' obrigatório"), 400

    # carregar áudio
    try:
        if os.path.exists(audio_ref):
            fobj = open(audio_ref, "rb")
        else:
            resp = requests.get(audio_ref, timeout=60); resp.raise_for_status()
            fobj = io.BytesIO(resp.content); fobj.name = Path(audio_ref).name
    except Exception as e:
        return jsonify(error="falha ao carregar áudio", detalhe=str(e)), 400

    try:
        srt = openai.audio.transcriptions.create(
            model="whisper-1", file=fobj, response_format="srt"
        )

        # parse original SRT
        orig_segments = []
        for block in srt.strip().split("\n\n"):
            lines = block.split("\n")
            if len(lines)<3: continue
            st,en = lines[1].split(" --> ")
            text = " ".join(lines[2:])
            orig_segments.append({
                "inicio": parse_ts(st),
                "fim":    parse_ts(en),
                "texto":  text
            })

        # resegmenta a cada 4 palavras
        new_segs = []
        for seg in orig_segments:
            words = seg["texto"].split()
            if not words: continue
            n = 4
            chunk_count = (len(words)+n-1)//n
            dur = seg["fim"] - seg["inicio"]
            for i in range(chunk_count):
                start = seg["inicio"] + dur * (i/chunk_count)
                end   = seg["inicio"] + dur * ((i+1)/chunk_count)
                txt   = " ".join(words[i*n:(i+1)*n])
                new_segs.append({"inicio": start, "fim": end, "texto": txt})

        # grava SRT no disco
        slug = request.args.get("slug") or Path(audio_ref).stem
        srt_path = Path(f"{slug}.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            for idx, seg in enumerate(new_segs, start=1):
                f.write(f"{idx}\n{fmt_ts(seg['inicio'])} --> {fmt_ts(seg['fim'])}\n{seg['texto']}\n\n")

        return jsonify(transcricao=new_segs)

    except Exception as e:
        return jsonify(error="falha na transcrição", detalhe=str(e)), 500

    finally:
        try: fobj.close()
        except: pass

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data       = request.get_json(force=True) or {}
    slug       = data.get("slug")
    transcricao= data.get("transcricao", [])
    prompts    = data.get("prompts", [])
    texto_orig = data.get("texto_original", "")
    if not slug or not transcricao or not prompts or len(transcricao)!=len(prompts):
        return jsonify(error="dados inválidos"), 400

    drive     = get_drive_service()
    folder_id = criar_pasta_drive(slug, drive)

    # 1) TXT: descrição para redes
    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4",
            messages=[
                {"role":"system","content":"Você é um assistente que gera legendas para redes sociais."},
                {"role":"user","content":
                    f"Escreva 2–3 frases sobre este texto para redes sociais, inclua no meio 'Siga @DeusTeEnviouIsso' e 5 hashtags no final:\n\n{texto_orig}"
                }
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

    # 2) CSV
    csv_path = Path(f"{slug}.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["TIME","PROMPT"])
        for seg, prompt in zip(transcricao, prompts):
            t = int(seg["inicio"])
            w.writerow([t, prompt])
    upload_para_drive(csv_path, csv_path.name, folder_id, drive)

    # 3) SRT e MP3 já gravados
    srt_path = Path(f"{slug}.srt")
    if srt_path.exists():
        upload_para_drive(srt_path, srt_path.name, folder_id, drive)
    mp3_path = Path(f"{slug}.mp3")
    if mp3_path.exists():
        upload_para_drive(mp3_path, mp3_path.name, folder_id, drive)

    return jsonify(
        slug=slug,
        folder_url=f"https://drive.google.com/drive/folders/{folder_id}"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0",
            port=int(os.getenv("PORT","5000")),
            debug=True)
