"""Speak one radio line with Piper (local) and the handheld-radio effect, for the reactive incident simulation.
    .venv/bin/python say.py <out.wav> <voice: ryan|lessac|joe> "<text>"
Same voices and effect as make_radio.py; ~1-2 s per line on the Nano's CPU."""
import io, os, sys, wave
import numpy as np
from piper import PiperVoice
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_radio import HERE, VOICES, radio_fx

out, voice, text = sys.argv[1], sys.argv[2], sys.argv[3]
v = PiperVoice.load(os.path.join(HERE, "voices", f"{VOICES.get(voice, VOICES['ryan'])}.onnx"))
buf = io.BytesIO()
with wave.open(buf, "wb") as w: v.synthesize_wav(text, w)
buf.seek(0)
with wave.open(buf, "rb") as w: sr = w.getframerate(); x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
y = radio_fx(x, sr, seed=abs(hash(text)) % 1000)
n = int(len(y) * 16000 / sr); y = np.interp(np.linspace(0, len(y) - 1, n), np.arange(len(y)), y)
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes((y * 32767).astype(np.int16).tobytes())
print(out, round(n / 16000, 1))
