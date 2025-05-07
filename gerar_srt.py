import whisperx
from pathlib import Path
from datetime import timedelta

# Caminho do áudio e saída
AUDIO_FILE = Path("audio/voz.mp3")
OUTPUT_SRT = Path("legendas/legenda.srt")

device = "cuda" if whisperx.utils.get_cuda_device_id() is not None else "cpu"

print(f"Transcrevendo {AUDIO_FILE}...")

# Transcrição
model = whisperx.load_model("base", device)
audio = whisperx.load_audio(str(AUDIO_FILE))
transcription = model.transcribe(audio)

# Alinhamento palavra a palavra
model_a, metadata = whisperx.load_align_model(language_code=transcription["language"], device=device)
aligned = whisperx.align(transcription["segments"], model_a, metadata, audio, device=device)

# Gerar legenda SRT com 1 palavra por linha
OUTPUT_SRT.parent.mkdir(parents=True, exist_ok=True)

def format_timestamp(t):
    return str(timedelta(seconds=t)).replace(".", ",")[:12].zfill(12)

with open(OUTPUT_SRT, "w", encoding="utf-8") as srt_file:
    for idx, word_info in enumerate(aligned["word_segments"], start=1):
        start = format_timestamp(word_info["start"])
        end = format_timestamp(word_info["end"])
        word = word_info["text"].strip()
        srt_file.write(f"{idx}\n{start} --> {end}\n{word}\n\n")

print(f"SRT gerado: {OUTPUT_SRT}")
