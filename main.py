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

    # abre local ou faz download
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
        raw_srt = openai.audio.transcriptions.create(
            model="whisper-1",
            file=fobj,
            response_format="srt"
        )
        # reagrupa cada ~3 palavras em seu próprio bloco SRT
        orig_blocks = []
        for blk in raw_srt.strip().split("\n\n"):
            parts = blk.split("\n")
            if len(parts) < 3: continue
            st, en = parts[1].split(" --> ")
            txt     = " ".join(parts[2:])
            inicio  = parse_ts(st)
            fim     = parse_ts(en)
            orig_blocks.append((inicio, fim, txt))

        srt_blocks = []
        idx = 1
        for inicio, fim, text in orig_blocks:
            words = text.split()
            group_size = 3
            num_groups = max(1, (len(words) + group_size - 1) // group_size)
            duration = fim - inicio
            for i in range(num_groups):
                chunk = words[i*group_size:(i+1)*group_size]
                if not chunk: break
                st_sub = inicio + duration * (i/num_groups)
                en_sub = inicio + duration * ((i+1)/num_groups)
                srt_blocks.append((idx, st_sub, en_sub, " ".join(chunk)))
                idx += 1

        # escreve o SRT no disco
        srt_path = Path(f"{slugify(audio_ref)}.srt")
        with open(srt_path, "w", encoding="utf-8") as f:
            for num, st, en, txt in srt_blocks:
                def fmt(s):
                    h = int(s//3600); m = int((s%3600)//60)
                    sec = int(s%60); ms = int((s%1)*1000)
                    return f"{h:02}:{m:02}:{sec:02},{ms:03}"
                f.write(f"{num}\n{fmt(st)} --> {fmt(en)}\n{txt}\n\n")

        total = srt_blocks[-1][2] if srt_blocks else 0
        return jsonify(
            transcricao=[{"inicio": st, "fim": en, "texto": txt}
                         for _, st, en, txt in srt_blocks],
            duracao_total=total
        )

    except Exception as e:
        return jsonify(error="falha na transcrição", detalhe=str(e)), 500

    finally:
        try: fobj.close()
        except: pass

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    aquarela_info = (
        "A imagem deve parecer uma pintura tradicional em aquarela, com foco em: "
        "Texturas suaves, como papel artesanal levemente poroso. "
        "Pinceladas visíveis e fluidas, com bordas levemente borradas. "
        "Cores vivas, mas com equilíbrio e transparência. "
        "Sensação de arte feita à mão."
    )
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
    neg = ("low quality, overexposed, underexposed, extra limbs, missing fingers, "
           "bad anatomy, realistic style, photographic style, text")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "TIME","PROMPT","VISIBILITY","ASPECT_RATIO",
            "MAGIC_PROMPT","MODEL","SEED_NUMBER","RENDERING",
            "NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"
        ])
        for seg, p in zip(transcricao, prompts):
            t = int(seg["inicio"])
                prompt_full = f"{p} {aquarela_info}"
            w.writerow([t, prompt_full, "PRIVATE","9:16","ON","3.0","","TURBO", neg, "AUTO", ""])
    upload_para_drive(csv_path, csv_path.name, folder_id, drive)

    # — Gera SRT (.srt) —
    def fmt(s):
        ms = int((s%1)*1000); h = int(s//3600)
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
