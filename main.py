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
    AudioFileClip, ImageClip, TextClip, CompositeVideoClip,
    concatenate_videoclips, VideoFileClip
)
from moviepy.video.VideoClip import VideoClip
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)

# Pastas locais
BASE = Path(".")
AUDIO_DIR = BASE / "audio"
CSV_DIR = BASE / "csv"
FILES_DIR = BASE / "downloads"
for d in [AUDIO_DIR, CSV_DIR, FILES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Configurações
GOOGLE_DRIVE_FOLDER_ID = "1d6RxnsYRS52oKUPGyuAfJZ00bksUUVI2"
ELEVEN_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
openai.api_key = OPENAI_KEY

# ---------- Bloco 1: Utilitários ----------

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        "/etc/secrets/service_account.json",
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)

def slugify(texto, limite=30):
    texto = unidecode.unidecode(texto)
    texto = re.sub(r"(?i)^deus\s+", "", texto)
    texto = re.sub(r"[^\w\s]", "", texto)
    texto = texto.strip().replace(" ", "_")
    return texto[:limite].lower()

def criar_pasta_drive(slug, drive):
    meta = {
        "name": f"deus_{slug}",
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [GOOGLE_DRIVE_FOLDER_ID]
    }
    pasta = drive.files().create(body=meta, fields="id").execute()
    return pasta.get("id")

def upload_arquivo_drive(filepath, filename, folder_id, drive):
    meta = {"name": filename, "parents": [folder_id]}
    media = MediaFileUpload(str(filepath), resumable=True)
    file = drive.files().create(body=meta, media_body=media, fields="id").execute()
    return file.get("id")

def format_ts(seconds):
    ms = int((seconds % 1) * 1000)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"

def make_grain(size=(1280, 720), intensity=10):
    def frame(t):
        noise = np.random.randint(128-intensity, 128+intensity, (size[1], size[0], 1), np.uint8)
        noise = np.repeat(noise, 3, axis=2)
        return noise
    return VideoClip(frame, duration=1).set_fps(24)

def selecionar_imagem_mais_similar(prompt, imagens):
    import re
    from sentence_transformers import SentenceTransformer, util
    model = SentenceTransformer("clip-ViT-B-32")
    p_emb = model.encode(prompt, convert_to_tensor=True)
    melhor, best_score = None, -1
    for img in imagens:
        nome = re.sub(r"[^\w\s]", " ", img.stem)
        i_emb = model.encode(nome, convert_to_tensor=True)
        score = util.cos_sim(p_emb, i_emb).item()
        if score > best_score:
            best_score, melhor = score, img
    return melhor

# ---------- Bloco 2: ElevenLabs TTS ----------

def elevenlabs_tts(text, voice_id="cwIsrQsWEVTols6slKYN", retries=3):
    def enviar(payload, desc):
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
        headers = {"xi-api-key": ELEVEN_API_KEY, "Content-Type": "application/json"}
        for i in range(retries):
            try:
                resp = requests.post(url, headers=headers, json=payload, stream=True, timeout=60)
                resp.raise_for_status()
                return resp.content
            except Exception:
                if i < retries-1: time.sleep(2**i)
                else: raise
    # tentativa com style
    p1 = {"text": text, "voice_settings": {"stability":0.6,"similarity_boost":0.9,"style":0.2}}
    try:
        audio = enviar(p1, "style")
        if audio: return audio
    except: pass
    p2 = {"text": text, "voice_settings": {"stability":0.6,"similarity_boost":0.9}}
    return enviar(p2, "no-style")

# ---------- Bloco 3: Endpoints básicos ----------

@app.route("/")
def home(): return "API OK"

@app.route("/audio/<path:fn>")
def servir_audio(fn): return send_from_directory(AUDIO_DIR, fn)

@app.route("/csv/<path:fn>")
def servir_csv(fn): return send_from_directory(CSV_DIR, fn)

@app.route("/downloads/<path:fn>")
def servir_down(fn): return send_from_directory(FILES_DIR, fn)

# ---------- Bloco 4: /falar ----------

@app.route("/falar", methods=["POST"])
def falar():
    data = request.get_json() or {}
    texto = data.get("texto")
    if not texto: return jsonify({"error":"campo 'texto' obrigatório"}),400
    slug = slugify(texto)
    filename = f"{slug}.mp3"
    path = AUDIO_DIR/filename
    try:
        audio = elevenlabs_tts(texto)
        with open(path,"wb") as f: f.write(audio)
    except Exception as e:
        return jsonify({"error":"falha TTS","detalhe":str(e)}),500
    return jsonify({
        "audio_url": f"{request.url_root}audio/{filename}",
        "filename": filename,
        "slug": slug
    })

# ---------- Bloco 5: /transcrever ----------

@app.route("/transcrever", methods=["POST"])
def transcrever():
    data = request.get_json() or {}
    url = data.get("audio_url")
    if not url: return jsonify({"error":"campo 'audio_url' obrigatório"}),400
    try:
        if url.startswith(request.url_root):
            name = url.rsplit("/audio/",1)[-1]
            f = open(AUDIO_DIR/name,"rb")
        else:
            r = requests.get(url,timeout=60); r.raise_for_status()
            f = io.BytesIO(r.content); f.name="audio.mp3"
        srt = openai.audio.transcriptions.create(
            model="whisper-1", file=f, response_format="srt"
        )
        # parse SRT
        def parse_ts(ts):
            h,m,rest=ts.split(":")
            s,ms=rest.split(",")
            return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
        segs=[]
        for blk in srt.strip().split("\n\n"):
            lines=blk.split("\n")
            if len(lines)>=3:
                a,b=lines[1].split(" --> ")
                txt=" ".join(lines[2:])
                segs.append({"inicio":parse_ts(a),"fim":parse_ts(b),"texto":txt})
        total=segs[-1]["fim"] if segs else 0
        return jsonify({"duracao_total":total,"transcricao":segs})
    except Exception as e:
        return jsonify({"error":str(e)}),500
    finally:
        try: f.close()
        except: pass

# ---------- Bloco 6: /gerar_csv ----------

@app.route("/gerar_csv", methods=["POST"])
def gerar_csv():
    data = request.get_json() or {}
    trans = data.get("transcricao",[])
    prompts = data.get("prompts",[])
    desc = data.get("descricao","")
    mp3_fn = data.get("mp3_filename")
    # auto-detect MP3
    if not mp3_fn:
        lst=list(AUDIO_DIR.glob("*.mp3"))
        if len(lst)==1: mp3_fn=lst[0].name
        else: return jsonify({"error":"mp3_filename obrigatório ou múltiplos"}),400
    slug = data.get("slug", Path(mp3_fn).stem)
    if not trans or len(trans)!=len(prompts):
        return jsonify({"error":"transcricao+prompts inválidos"}),400
    p = AUDIO_DIR/mp3_fn
    if not p.exists(): return jsonify({"error":"MP3 não encontrado"}),400
    drive = get_drive_service()
    fid = criar_pasta_drive(slug,drive)
    csvp = CSV_DIR/f"{slug}.csv"
    srtp = FILES_DIR/f"{slug}.srt"
    txtp = FILES_DIR/f"{slug}.txt"
    # CSV
    hdr=["PROMPT","VISIBILITY","ASPECT_RATIO","MAGIC_PROMPT","MODEL",
         "SEED_NUMBER","RENDERING","NEGATIVE_PROMPT","STYLE","COLOR_PALETTE"]
    neg="low quality, overexposed, underexposed, extra limbs"
    with open(csvp,"w",newline="",encoding="utf-8") as f:
        w=csv.writer(f); w.writerow(hdr)
        for seg,prompt in zip(trans,prompts):
            sec=int(round(seg["inicio"]))
            row=f"{sec} - Painting style: Traditional watercolor, with soft brush strokes and handmade paper texture. {prompt}"
            w.writerow([row,"PRIVATE","9:16","ON","3.0","","TURBO",neg,"AUTO",""])
    # SRT
    with open(srtp,"w",encoding="utf-8") as s:
        for i,seg in enumerate(trans,1):
            s.write(f"{i}\n{format_ts(seg['inicio'])} --> {format_ts(seg['fim'])}\n{seg['texto']}\n\n")
    # TXT
    with open(txtp,"w",encoding="utf-8") as t:
        t.write(desc.strip())
    # uploads
    upload_arquivo_drive(csvp,"imagens.csv",fid,drive)
    upload_arquivo_drive(srtp,"legenda.srt",fid,drive)
    upload_arquivo_drive(txtp,"descricao.txt",fid,drive)
    upload_arquivo_drive(p,"voz.mp3",fid,drive)
    return jsonify({"folder_url":f"https://drive.google.com/drive/folders/{fid}"})

# ---------- Bloco 7: /upload_zip ----------

@app.route("/upload_zip", methods=["POST"])
def upload_zip():
    file = request.files.get("zip")
    if not file: return jsonify({"error":"Campo 'zip' obrigatório."}),400
    projetos=[p for p in FILES_DIR.iterdir() if p.is_dir() and not p.name.endswith("_raw")]
    if len(projetos)!=1:
        return jsonify({"error":"Esperado 1 pasta de projeto, achado %d."%len(projetos)}),400
    slug = projetos[0].name
    temp = FILES_DIR/f"{slug}_raw"
    out  = FILES_DIR/slug
    temp.mkdir(exist_ok=True); out.mkdir(exist_ok=True)
    zpath = temp/"imagens.zip"; file.save(zpath)
    with zipfile.ZipFile(zpath,"r") as z: z.extractall(temp)
    imgs = [f for f in temp.glob("*.*") if f.suffix.lower() in [".jpg",".jpeg",".png"]]
    if not imgs: return jsonify({"error":"Nenhuma imagem no ZIP."}),400
    csvp = CSV_DIR/f"{slug}.csv"
    if not csvp.exists(): return jsonify({"error":"CSV não encontrado."}),400
    prompts=[]
    with open(csvp,newline="",encoding="utf-8") as f:
        rd=csv.DictReader(f)
        for r in rd: prompts.append(r["PROMPT"].split(" - ",1)[-1].strip())
    usadas=[]
    for i,prompt in enumerate(prompts):
        best=selecionar_imagem_mais_similar(prompt,imgs)
        if best:
            dst=out/f"{i:02d}_{best.name}"
            best.rename(dst); imgs.remove(best); usadas.append(dst.name)
    return jsonify({"ok":True,"slug":slug,"usadas":usadas})

# ---------- Bloco 8: /montar_video ----------

@app.route("/montar_video", methods=["POST"])
def montar_video():
    from difflib import SequenceMatcher
    def sim(a,b): return SequenceMatcher(None,a.lower(),b.lower()).ratio()
    data=request.get_json(force=True)
    slug, fid = data.get("slug"), data.get("folder_id")
    folder=FILES_DIR/slug
    imgs=sorted([f for f in folder.iterdir() if f.suffix.lower() in ['.jpg','.jpeg','.png']])
    mp3s=list(AUDIO_DIR.glob("*.mp3"))
    if not mp3s: return jsonify({"error":"Nenhum áudio."}),400
    audio=mp3s[0]
    srtf=list(FILES_DIR.glob("*.srt"))
    if not srtf: return jsonify({"error":"Nenhuma SRT."}),400
    csvs=list(CSV_DIR.glob("*.csv"))
    if not csvs: return jsonify({"error":"Nenhum CSV."}),400
    # lê prompts
    prompts=[]
    with open(csvs[0],newline="",encoding="utf-8") as f:
        rd=csv.reader(f); next(rd)
        for r in rd: prompts.append(r[0].split(" - ",1)[-1])
    # associa
    assoc=[]; used=set()
    for p in prompts:
        mv=max([i for i in imgs if i not in used], key=lambda x:sim(p,x.stem), default=None)
        assoc.append(mv or imgs[0]); used.add(mv or imgs[0])
    # lê SRT
    trans=[]
    with open(srtf[0],encoding="utf-8") as f:
        for blk in f.read().split("\n\n"):
            ln=blk.split("\n")
            if len(ln)>=3:
                a,b=ln[1].split(" --> ")
                def pt(ts): h,m,r=ts.split(":"); s,ms=r.split(","); return int(h)*3600+int(m)*60+int(s)+int(ms)/1000
                trans.append({"inicio":pt(a),"fim":pt(b),"texto":" ".join(ln[2:])})
    ac = AudioFileClip(str(audio))
    clips=[]
    for i,seg in enumerate(trans):
        dur=seg["fim"]-seg["inicio"]
        img=ImageClip(str(assoc[i%len(assoc)])).resize(height=720).crop(x_center='center',width=1280).set_duration(dur)
        zoom=img.resize(lambda t:1+0.02*t)
        txt=TextClip(seg["texto"].upper(),fontsize=60,font='DejaVu-Sans-Bold',color='white',
                     stroke_color='black',stroke_width=2,method='caption',size=(1280,None)
                    ).set_duration(dur).set_position(('center','bottom'))
        grain=make_grain().set_opacity(0.05).set_duration(dur)
        luz=VideoFileClip("sobrepor.mp4").resize((1280,720)).set_opacity(0.07).set_duration(dur)
        marca=ImageClip("sobrepor.png").resize(height=100).set_position((20,20)).set_duration(dur)
        comp=CompositeVideoClip([zoom,grain,luz,marca,txt],size=(1280,720))
        clips.append(comp)
    # encerramento
    end_img=ImageClip("fechamento.png").resize(height=720).crop(x_center='center',width=1280).set_duration(3)
    gf=make_grain().set_opacity(0.05).set_duration(3)
    lf=VideoFileClip("sobrepor.mp4").resize((1280,720)).set_opacity(0.07).set_duration(3)
    end=CompositeVideoClip([end_img,gf,lf],size=(1280,720))
    final=concatenate_videoclips(clips+[end]).set_audio(ac)
    outp=FILES_DIR/f"{slug}.mp4"
    final.write_videofile(str(outp),fps=24,codec='libx264',audio_codec='aac')
    drive=get_drive_service()
    upload_arquivo_drive(outp,"video_final.mp4",fid,drive)
    return jsonify({"ok":True,"folder":f"https://drive.google.com/drive/folders/{fid}"})

# ---------- Bloco 9: Plugin JSON ----------

@app.route('/.well-known/ai-plugin.json')
def serve_ai_plugin():
    return send_from_directory('.well-known','ai-plugin.json',mimetype='application/json')

@app.route('/.well-known/openapi.json')
def serve_openapi():
    return send_from_directory('.well-known','openapi.json',mimetype='application/json')

if __name__ == "__main__":
    app.run(host="0.0.0.0",port=int(os.getenv("PORT",5000)),debug=True)