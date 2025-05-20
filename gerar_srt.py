import os
import whisperx
from pathlib import Path
from datetime import timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Configurações do Google Drive
SERVICE_ACCOUNT_FILE = "/etc/secrets/service_account.json"
GOOGLE_DRIVE_ROOT_FOLDER = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def criar_subpasta(nome: str, drive, parent_folder_id: str):
    meta = {
        "name": nome,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [parent_folder_id]
    }
    return drive.files().create(body=meta).execute()["id"]

def upload_para_drive(path: Path, nome: str, folder_id: str, drive):
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(body={"name": nome, "parents": [folder_id]}, media_body=media).execute()

# Função para obter o slug do texto (deve ser a mesma do main.py)
def slugify(text: str, limit: int = 30) -> str:
    import unidecode
    import re
    from datetime import datetime
    import uuid
    
    if not text:
        return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:6]
    
    txt = unidecode.unidecode(text)
    txt = re.sub(r"[^\w\s]", "", txt)
    txt = txt.strip().replace(" ", "_").lower()
    return txt[:limit] if txt else datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:6]

# Parâmetros de entrada
# Estes parâmetros devem ser passados como argumentos ou variáveis de ambiente
AUDIO_FILE = Path("audio/voz.mp3")
TEXTO_ORIGINAL = os.getenv("TEXTO_ORIGINAL", "")  # Texto original para gerar o slug
SLUG = os.getenv("SLUG", "")  # Slug pré-definido (opcional)

# Se não tiver slug, gera a partir do texto original
if not SLUG and TEXTO_ORIGINAL:
    SLUG = slugify(TEXTO_ORIGINAL)
elif not SLUG:
    # Fallback: usa o nome do arquivo de áudio
    SLUG = slugify(AUDIO_FILE.stem)

# Define os caminhos de saída usando o slug
OUTPUT_SRT = Path(f"{SLUG}_legenda.srt")
OUTPUT_TXT = Path(f"{SLUG}_transcricao.txt")

print(f"Usando slug: {SLUG}")
print(f"Transcrevendo {AUDIO_FILE}...")

device = "cuda" if whisperx.utils.get_cuda_device_id() is not None else "cpu"

# Transcrição
model = whisperx.load_model("base", device)
audio = whisperx.load_audio(str(AUDIO_FILE))
transcription = model.transcribe(audio)

# Alinhamento palavra a palavra
model_a, metadata = whisperx.load_align_model(language_code=transcription["language"], device=device)
aligned = whisperx.align(transcription["segments"], model_a, metadata, audio, device=device)

# Gerar legenda SRT com 1 palavra por linha
def format_timestamp(t):
    return str(timedelta(seconds=t)).replace(".", ",")[:12].zfill(12)

# Salvar arquivo SRT
with open(OUTPUT_SRT, "w", encoding="utf-8") as srt_file:
    for idx, word_info in enumerate(aligned["word_segments"], start=1):
        start = format_timestamp(word_info["start"])
        end = format_timestamp(word_info["end"])
        word = word_info["text"].strip()
        srt_file.write(f"{idx}\n{start} --> {end}\n{word}\n\n")

print(f"SRT gerado: {OUTPUT_SRT}")

# Salvar transcrição completa em TXT
with open(OUTPUT_TXT, "w", encoding="utf-8") as txt_file:
    for segment in transcription["segments"]:
        txt_file.write(f"{segment['text']}\n")

print(f"TXT gerado: {OUTPUT_TXT}")

# Upload para o Google Drive
try:
    print("Iniciando upload para o Google Drive...")
    drive = get_drive_service()
    
    # Verificar se a pasta já existe ou criar nova
    try:
        # Tentar encontrar pasta existente com o mesmo slug
        results = drive.files().list(
            q=f"name='{SLUG}' and mimeType='application/vnd.google-apps.folder' and '{GOOGLE_DRIVE_ROOT_FOLDER}' in parents",
            spaces='drive',
            fields='files(id, name)'
        ).execute()
        
        items = results.get('files', [])
        if items:
            folder_id = items[0]['id']
            print(f"Pasta existente encontrada: {folder_id}")
        else:
            # Criar nova pasta
            folder_id = criar_subpasta(SLUG, drive, GOOGLE_DRIVE_ROOT_FOLDER)
            print(f"Nova pasta criada: {folder_id}")
    except Exception as e:
        print(f"Erro ao verificar/criar pasta: {e}")
        # Fallback: criar nova pasta
        folder_id = criar_subpasta(SLUG, drive, GOOGLE_DRIVE_ROOT_FOLDER)
    
    # Upload do arquivo SRT
    upload_para_drive(OUTPUT_SRT, OUTPUT_SRT.name, folder_id, drive)
    print(f"SRT enviado para o Drive: {OUTPUT_SRT.name}")
    
    # Upload do arquivo TXT
    upload_para_drive(OUTPUT_TXT, OUTPUT_TXT.name, folder_id, drive)
    print(f"TXT enviado para o Drive: {OUTPUT_TXT.name}")
    
    print(f"Upload concluído. Pasta do Drive: https://drive.google.com/drive/folders/{folder_id}")
    
except Exception as e:
    print(f"Erro no upload para o Drive: {e}")
