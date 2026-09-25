"""RescueGrid ASR service: OpenAI-compatible /v1/audio/transcriptions backed by faster-whisper.

Runs outside ZRT because ZRT's proxy does not route audio endpoints and its vLLM
venv lacks vllm[audio]. CTranslate2's aarch64 wheel is CPU-only, so this runs int8
on CPU (~0.27x real-time on the ZGX Nano) and uses no GPU memory.

    .venv/bin/uvicorn server:app --host 127.0.0.1 --port 8090
"""

import os
import tempfile
import threading
import time

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from faster_whisper import WhisperModel

MODEL_NAME = os.environ.get("ASR_MODEL", "large-v3-turbo")
CPU_THREADS = int(os.environ.get("ASR_CPU_THREADS", "12"))

app = FastAPI(title="RescueGrid ASR")
model = WhisperModel(MODEL_NAME, device="cpu", compute_type="int8", cpu_threads=CPU_THREADS)
model_lock = threading.Lock()  # one transcription at a time; parallel runs just contend for cores


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_NAME}


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": "asr", "object": "model", "owned_by": "faster-whisper"}]}


@app.post("/v1/audio/transcriptions")
def transcribe(
    file: UploadFile = File(...),
    model_id: str = Form("asr", alias="model"),
    language: str = Form("en"),
    response_format: str = Form("json"),
):
    suffix = os.path.splitext(file.filename or "")[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        tmp.write(file.file.read())
        tmp.flush()
        started = time.time()
        try:
            with model_lock:
                segments, info = model.transcribe(tmp.name, beam_size=1, language=language or None, vad_filter=True)
                segments = list(segments)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Could not transcribe audio: {e}")
        elapsed = time.time() - started

    text = " ".join(s.text.strip() for s in segments)
    if response_format == "text":
        return text
    if response_format != "verbose_json":
        return {"text": text}
    return {
        "task": "transcribe",
        "language": info.language,
        "duration": info.duration,
        "text": text,
        "segments": [
            {"id": i, "start": s.start, "end": s.end, "text": s.text.strip(), "avg_logprob": s.avg_logprob}
            for i, s in enumerate(segments)
        ],
        "processing_seconds": round(elapsed, 3),
    }
