import os
import io
import csv
import re
import requests
import unidecode
import json
from pathlib import Path
from flask import Flask, request, jsonify
from openai import OpenAI
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
import math

app = Flask(__name__)

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

GOOGLE_DRIVE_ROOT_FOLDER = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
SERVICE_ACCOUNT_FILE     = "/etc/secrets/service_account.json"
ELEVEN_API_KEY           = os.getenv("ELEVENLABS_API_KEY")

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def upload_para_drive(path: Path, nome: str, folder_id: str, drive):
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(
        body={"name": nome, "parents": [folder_id]},
        media_body=media
    ).execute()

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
    s, ms = rest.split(",")
    return int(h)*3600 + int(m)*60 + int(s) + int(ms)/1000

@app.route("/")
def home():
    return "API DeusTeEnviouIsso OK"

@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json(force=True) or {}
    texto = data.get("texto")
    if not texto:
        return jsonify(error="campo 'texto' obrigatório"), 400

    slug = slugify(texto)
    if not slug:
        return jsonify(error="slug vazio gerado a partir do texto."), 400

    mp3_path = Path(f"{slug}_audio.mp3")

    try:
        if not ELEVEN_API_KEY:
            raise Exception("ELEVEN_API_KEY não está definido")
        audio_bytes = elevenlabs_tts(texto)
        if not audio_bytes or len(audio_bytes) < 1000:
            raise Exception("Áudio gerado é vazio ou muito pequeno.")
        mp3_path.write_bytes(audio_bytes)
    except Exception as e:
        return jsonify(error="falha ElevenLabs", detalhe=str(e)), 500

    try:
        drive = get_drive_service()
        folder_id = GOOGLE_DRIVE_ROOT_FOLDER
        upload_para_drive(mp3_path, mp3_path.name, folder_id, drive)
    except Exception as e:
        return jsonify(error="falha no upload do MP3 para o Drive", detalhe=str(e)), 500

    return jsonify(
        audio_url=str(mp3_path.resolve()),
        slug=slug,
        drive_folder_url=f"https://drive.google.com/drive/folders/{folder_id}"
    )

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    aquarela_info = (
        "A imagem deve parecer uma pintura tradicional em aquarela, com foco em: "
        "Texturas suaves, como papel artesanal levemente poroso. "
        "Pinceladas visíveis e fluidas, com bordas levemente borradas. "
        "Cores vivas, mas com equilíbrio e transparência. "
        "Sensação de arte feita à mão."
    )
    data = request.get_json() or {}
    transcricao = data.get("transcricao", [])
    texto_orig = data.get("texto_original", "")

    if not transcricao:
        return jsonify(error="transcricao inválida"), 400

    slug = slugify(texto_orig)
    if not slug:
        return jsonify(error="slug vazio gerado a partir do texto original."), 400

    drive = get_drive_service()
    folder_id = GOOGLE_DRIVE_ROOT_FOLDER

    duracao_total = transcricao[-1]["fim"]
    intervalo = 4
    prompts_count = math.ceil(duracao_total / intervalo)

    resumo_ts = "\n".join([
        f"{seg['inicio']:.2f}-{seg['fim']:.2f}: {seg['texto']}"
        for seg in transcricao
    ])

    resp_prompts = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "Você é um assistente que transforma transcrições em prompts de ilustração dinâmicos."},
            {"role": "user", "content":
             f"Dada esta transcrição com tempos (em segundos):\n\n{resumo_ts}\n\n"
             f"Gere exatamente {prompts_count} prompts de ilustração em português, "
             f"um prompt para cada intervalo de {intervalo} segundos de vídeo. "
             "De forma artística, que inspire e gere vontade de compartilhar. "
             "Responda SOMENTE um JSON no formato:\n"
             "[{\"t\": número_de_segundos, \"prompt\": \"descrição da imagem\"}, ...]"}
        ],
        temperature=0.7
    )
    prompts_data = json.loads(resp_prompts.choices[0].message.content)

    # Descrição social para redes
    resp_descricao = client.chat.completions.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "Você é um redator cristão que cria descrições curtas e inspiradoras para redes sociais."},
            {"role": "user", "content":
             f"Com base no texto a seguir, gere uma descrição de no máximo 2 linhas, que inspire e tenha ligação com fé, superação ou propósito. "
             f"No meio, insira exatamente a frase: '🙏 Siga @DeusTeEnviouIsso para mais conteúdo de fé no seu dia-a-dia!'. "
             f"No final, adicione 6 hashtags relevantes ao conteúdo. Responda apenas com o conteúdo final.\n\nTexto original:\n{texto_orig}"}
        ],
        temperature=0.8
    )
    descricao_social = resp_descricao.choices[0].message.content.strip()
    txt_desc_path = Path(f"{slug}_descricao.txt")
    with open(txt_desc_path, "w", encoding="utf-8") as f:
        f.write(descricao_social)
    upload_para_drive(txt_desc_path, txt_desc_path.name, folder_id, drive)

    # Gera CSV
    csv_path = Path(f"{slug}_prompts.csv")
    neg = "low quality, overexposed, underexposed, extra limbs, missing fingers, bad anatomy, realistic style, photographic style, text"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["PROMPT", "VISIBILITY", "ASPECT_RATIO", "MAGIC_PROMPT", "MODEL", "SEED_NUMBER",
                    "RENDERING", "NEGATIVE_PROMPT", "STYLE", "COLOR_PALETTE", "Num_images"])
        for item in prompts_data:
            segundos = int(round(item["t"]))
            prompt_full = f"{segundos} {item['prompt']} {aquarela_info}"
            w.writerow([prompt_full, "PRIVATE", "9:16", "ON", "3.0", "", "TURBO", neg, "AUTO", "", "4"])
    upload_para_drive(csv_path, csv_path.name, folder_id, drive)

    return jsonify(slug=slug, folder_url=f"https://drive.google.com/drive/folders/{folder_id}")

@app.route("/listar_arquivos_drive", methods=["GET"])
def listar_arquivos_drive():
    try:
        drive = get_drive_service()
        arquivos = []
        page_token = None
        while True:
            response = drive.files().list(
                q=f"'{GOOGLE_DRIVE_ROOT_FOLDER}' in parents and trashed = false",
                spaces="drive",
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token
            ).execute()

            for file in response.get("files", []):
                link = f"https://drive.google.com/file/d/{file['id']}/view" \
                    if file["mimeType"] != "application/vnd.google-apps.folder" \
                    else f"https://drive.google.com/drive/folders/{file['id']}"
                arquivos.append({
                    "nome": file["name"],
                    "tipo": "pasta" if file["mimeType"].endswith(".folder") else "arquivo",
                    "link": link
                })

            page_token = response.get("nextPageToken", None)
            if page_token is None:
                break

        return jsonify(arquivos=arquivos)

    except Exception as e:
        return jsonify(error="falha ao listar arquivos", detalhe=str(e)), 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)