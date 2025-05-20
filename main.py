import os
import io
import csv
import re
import requests
import unidecode
import json
import uuid
import math
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify
from openai import OpenAI
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

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

def criar_pasta_se_preciso(pasta_alvo, drive):
    try:
        drive.files().get(fileId=pasta_alvo, fields="id").execute()
    except HttpError:
        meta = {
            "name": "DEUS_TTS_AUTOGERADA",
            "mimeType": "application/vnd.google-apps.folder"
        }
        pasta_alvo = drive.files().create(body=meta).execute()["id"]
    return pasta_alvo

def upload_para_drive(path: Path, nome: str, folder_id: str, drive):
    media = MediaFileUpload(str(path), resumable=True)
    drive.files().create(
        body={"name": nome, "parents": [folder_id]},
        media_body=media
    ).execute()

def gerar_slug():
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + str(uuid.uuid4())[:6]
