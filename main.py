# main.py

import os
import uuid
import io
import csv
import re
import zipfile
import time
import requests
import unidecode
import numpy as np
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
import openai
from moviepy.editor import (
    AudioFileClip, ImageClip, TextClip,
    CompositeVideoClip, concatenate_videoclips,
    VideoFileClip
)
from moviepy.video.VideoClip import VideoClip
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from sentence_transformers import SentenceTransformer, util
from difflib import SequenceMatcher

app = Flask(__name__)

# ── Configurações iniciais ─────────────────────────────────────────────────────

openai.api_key = os.getenv("OPENAI_API_KEY")
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"

BASE      = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR   = BASE / "csv"
FILES_DIR = BASE / "downloads"
for d in [AUDIO_DIR, CSV_DIR, FILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# carrega CLIP UMA única vez
clip_model = SentenceTransformer("clip-ViT-B-32")


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(texto, limite=30):
    s = unidecode.unidecode(texto)
    s = re.sub(r"[^\w\s]", "", s)
    s = s.strip().replace(" ", "_")
    return s[:limite].lower()

def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280,720), intensity=10):
    def frame(t):
        n = np.random.randint(128-intensity, 128+intensity, (size[1],size[0],1), dtype=np.uint8)
        return np.repeat(n,3,axis=2)
    return VideoClip(frame, duration=1).set_fps(24)

def selecionar_imagem_mais_similar(prompt, imagens):
    p_emb = clip_model.encode(prompt, convert_to_tensor=True)
    best, best_score = None, -1.0
    for img in imagens:
        name = re.sub(r"[^\w\s]", " ", img.stem)
        i_emb = clip_model.encode(name, convert_to_tensor=True)
        score = util.cos_sim(p_emb, i_emb).item()
        if score > best_score:
            best_score, best = score, img
    return best

def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN", retries=3):
    def _req(payload):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type":"application/json"}
        for i in range(retries):
            r = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
            if r.ok: return r.content
            time.sleep(2**i)
        raise RuntimeError("ElevenLabs TTS failed")
    p1 = {"text":text, "voice_settings":{"stability":0.6,"similarity_boost":0.9,"style":0.2}}
    try:
        a = _req(p1)
        if not a: raise
        return a
    except:
        p2 = {"text":text, "voice_settings":{"stability":0.6,"similarity_boost":0.9}}
        a = _req(p2)
        if not a: raise
        return a


# ── Endpoints de texto ─────────────────────────────────────────────────────────

@app.route("/sugerir_versiculos", methods=["POST"])
def sugerir_versiculos():
    tema = (request.json or {}).get("tema","").strip()
    if not tema:
        return jsonify({"error":"campo 'tema' obrigatório"}),400
    prompt = (
        f"Liste 15 versículos bíblicos completos sobre “{tema}”, "
        "cada um em nova linha, formato “Livro Cap:Ver Texto”."
    )
    r = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        messages=[{"role":"user","content":prompt}]
    )
    lines = [l.strip() for l in r.choices[0].message.content.split("\n") if l.strip()]
    return jsonify({"versiculos": lines})

@app.route("/gerar_texto", methods=["POST"])
def gerar_texto():
    d = request.json or {}
    modo = d.get("modo")
    inp  = (d.get("input") or "").strip()

    if modo == "frase":
        sv = sugerir_versiculos().get_json().get("versiculos",[])[:3]
        prompt = f"Escreva um texto inspirador usando estes 3 versículos:\n" + "\n".join(sv)
        c = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}]
        )
        return jsonify({
            "versiculos": sv,
            "texto": c.choices[0].message.content,
            "perguntar_voz": True
        })

    elif modo == "versiculo":
        if not inp:
            return jsonify({"error":"campo 'input' obrigatório para 'versiculo'"}),400
        prompt = f"Escreva um texto meditativo baseado no versículo {inp}."
        c = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}]
        )
        return jsonify({"texto": c.choices[0].message.content, "perguntar_voz": True})

    elif modo == "aleatorio":
        prompt = (
            "Escolha um tema bíblico + um versículo relevante, "
            "e escreva um texto reflexivo. Retorne JSON com "
            "{\"versiculo\":...,\"texto\":...}."
        )
        c = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}]
        )
        return jsonify(c.choices[0].message.content)

    else:
        return jsonify({"error":"modo inválido"}),400


# ── Endpoints de áudio e transcrição ─────────────────────────────────────────

@app.route("/falar", methods=["POST"])
def falar():
    t = (request.json or {}).get("texto","").strip()
    if not t: return jsonify({"error":"texto obrigatório"}),400
    slug     = slugify(t)
    fname    = f"{slug}.mp3"
    out_path = AUDIO_DIR / fname
    try:
        data = elevenlabs_tts(t)
    except Exception as e:
        return jsonify({"error":"TTS falhou","detalhe":str(e)}),500
    out_path.write_bytes(data)
    return jsonify({
        "audio_url": request.url_root.rstrip("/")+f"/audio/{fname}",
        "filename": fname,
        "slug": slug
    })

@app.route("/transcrever", methods=["POST"])
def transcrever():
    url = (request.json or {}).get("audio_url","")
    if not url: return jsonify({"error":"audio_url obrigatório"}),400
    if url.startswith(request.url_root.rstrip("/")):
        fn = url.rsplit("/audio/",1)[-1]
        f  = open(AUDIO_DIR/fn,"rb")
    else:
        r  = requests.get(url,timeout=60); r.raise_for_status()
        f  = io.BytesIO(r.content); f.name="audio.mp3"
    res = openai.audio.transcriptions.create(
        model="whisper-1", file=f,
        response_format="verbose_json", timestamp_granularities=["segment"]
    )
    f.close()
    segs = [{"inicio":s.start,"fim":s.end,"texto":s.text} for s in res.segments]
    return jsonify({"duracao_total":res.duration,"transcricao":segs})


# ── Endpoints CSV / SRT / TXT & Drive ──────────────────────────────────────────

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    d      = request.json or {}
    segs   = d.get("transcricao",[])
    prompts= d.get("prompts",[])
    desc   = d.get("descricao","")
    # detecta mp3 único
    mp3s   = list(AUDIO_DIR.glob("*.mp3"))
    if len(mp3s)!=1:
        return jsonify({"error":"existe mais/menos de 1 mp3; especifique mp3_filename"}),400
    slug   = Path(mp3s[0]).stem
    drive  = get_drive_service()
    fid    = drive.files().create(
        body={"name":slug,"mimeType":"application/vnd.google-apps.folder",
              "parents":[GOOGLE_DRIVE_FOLDER_ID]},fields="id"
    ).execute()["id"]

    # CSV, SRT, TXT
    csvp = CSV_DIR/f"{slug}.csv"
    srtp= FILES_DIR/f"{slug}.srt"
    txtp= FILES_DIR/f"{slug}.txt"

    neg = "low quality,overexposed,underexposed,extra limbs,missing fingers,bad anatomy"
    with open(csvp,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f)
        w.writerow(["PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT",
                    "MODEL","SEED_NUMBER","RENDERING","NEGATIVE_PROMPT",
                    "STYLE","COLOR_PALETTE"])
        for seg,p in zip(segs,prompts):
            s=int(round(seg["inicio"]))
            w.writerow([f"{s} - {p}","PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])
    with open(srtp,"w",encoding="utf-8") as f:
        for i,seg in enumerate(segs,1):
            f.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")
    txtp.write_text(desc,encoding="utf-8")

    # upload
    for path,name in [(csvp,"imagens.csv"),(srtp,"legenda.srt"),(txtp,"descricao.txt"),(mp3s[0],"voz.mp3")]:
        MediaFileUpload and drive.files().create(
            body={"name":name,"parents":[fid]},
            media_body=MediaFileUpload(str(path),resumable=True),
            fields="id"
        ).execute()

    return jsonify({"folder_url":f"https://drive.google.com/drive/folders/{fid}"})


# ── Upload .zip de imagens + seleção via CLIP ─────────────────────────────────

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    f = request.files.get("zip")
    if not f: return jsonify({"error":"campo 'zip' obrigatório"}),400
    projetos = [p for p in FILES_DIR.iterdir() if p.is_dir() and not p.name.endswith("_raw")]
    if len(projetos)!=1:
        return jsonify({"error":"deve existir exatamente uma pasta de projeto pré-criada"}),400
    slug = projetos[0].name
    raw  = FILES_DIR/f"{slug}_raw"; raw.mkdir(exist_ok=True)
    out  = FILES_DIR/slug;      out.mkdir(exist_ok=True)
    zp   = raw/"imgs.zip"; f.save(zp)
    with zipfile.ZipFile(zp,"r") as z: z.extractall(raw)
    imgs = [x for x in raw.iterdir() if x.suffix.lower() in (".jpg",".png",".jpeg")]
    if not imgs: return jsonify({"error":"nenhuma imagem no ZIP"}),400

    # lê prompts do CSV
    csvp = CSV_DIR/f"{slug}.csv"
    rows = list(csv.DictReader(open(csvp,encoding="utf-8")))
    prompts = [r["PROMPT"].split(" - ",1)[-1] for r in rows]

    usadas=[]
    for i,p in enumerate(prompts):
        best = selecionar_imagem_mais_similar(p, imgs)
        if best:
            dest = out/f"{i:02d}_{best.name}"
            best.rename(dest); usadas.append(dest.name)

    return jsonify({"ok":True,"selecionadas":usadas,"slug":slug})


# ── Montagem de vídeo final ─────────────────────────────────────────────────────

@app.route("/montar_video", methods=["POST"])
def montar_video():
    d = request.json or {}
    slug      = d.get("slug")
    folder_id = d.get("folder_id")
    if not slug or not folder_id:
        return jsonify({"error":"slug e folder_id obrigatórios"}),400

    # imagens
    imgs = sorted((FILES_DIR/slug).glob("*.*"))
    if not imgs:
        return jsonify({"error":"nenhuma imagem encontrada"}),400
    # áudio
    mp3s = list(AUDIO_DIR.glob("*.mp3"))
    if not mp3s: return jsonify({"error":"áudio não encontrado"}),400

    # transcrição
    srtf = list(FILES_DIR.glob("*.srt"))
    if not srtf: return jsonify({"error":"legenda .srt não encontrada"}),400

    # monta
    audio = AudioFileClip(str(mp3s[0]))
    clips=[]
    # parse SRT
    txt = open(srtf[0],encoding="utf-8").read().split("\n\n")
    segs=[]
    for blk in txt:
        lines=blk.split("\n")
        if len(lines)>=3:
            t0,t1 = lines[1].split(" --> ")
            def p(ts): h,m,rest=ts.split(":"); s,ms=rest.split(","); return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
            segs.append({"inicio":p(t0),"fim":p(t1),"texto":" ".join(lines[2:])})
    for i,seg in enumerate(segs):
        dur=seg["fim"]-seg["inicio"]
        img= ImageClip(str(imgs[i%len(imgs)])).resize(height=720).crop(x_center='center',width=1280).set_duration(dur)
        zoom= img.resize(lambda t:1+0.02*t)
        txtc= TextClip(seg["texto"].upper(),fontsize=60, font='DejaVu-Sans-Bold',
                       color='white',stroke_color='black',stroke_width=2,
                       size=(1280,None), method='caption'
                   ).set_duration(dur).set_position(('center','bottom'))
        clips.append(CompositeVideoClip([zoom, make_grain().set_opacity(0.05).set_duration(dur), txtc],
                                       size=(1280,720)))
    final = concatenate_videoclips(clips + [
        CompositeVideoClip([
            ImageClip("fechamento.png").resize(height=720).crop(x_center='center',width=1280),
            make_grain().set_opacity(0.05).set_duration(3)
        ], size=(1280,720))
    ]).set_audio(audio)
    outp = FILES_DIR/f"{slug}.mp4"
    final.write_videofile(str(outp), fps=24, codec='libx264', audio_codec='aac')

    # upload ao Drive
    svc = get_drive_service()
    MediaFileUpload and svc.files().create(
        body={"name":"video_final.mp4","parents":[folder_id]},
        media_body=MediaFileUpload(str(outp),resumable=True),
        fields="id"
    ).execute()

    return jsonify({"ok":True,
                    "video_url":f"https://drive.google.com/drive/folders/{folder_id}"})


# ── Plugin & OpenAPI ──────────────────────────────────────────────────────────

@app.route('/.well-known/ai-plugin.json')
def serve_ai_plugin():
    return send_from_directory('.well-known','ai-plugin.json',mimetype='application/json')

@app.route('/.well-known/openapi.json')
def serve_openapi():
    return send_from_directory('.well-known','openapi.json',mimetype='application/json')


if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)
