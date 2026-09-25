"""Generate the built-in radio library: EOC channel-3 transmissions spoken by Piper (local neural TTS, no cloud), then
shaped like a handheld radio (300-3400 Hz band, compression, hiss, squelch click). Output: library/<id>.wav + library.json.
These files are ASSETS: at demo time they go through the real pipeline (faster-whisper ASR -> LLM claim extraction ->
fusion agent -> graph), exactly like an uploaded recording would.
    .venv/bin/python make_radio.py            # writes library/
"""
import json, os, wave, io, math, random
import numpy as np
from piper import PiperVoice

HERE = os.path.dirname(os.path.abspath(__file__)); OUT = os.path.join(HERE, "library"); os.makedirs(OUT, exist_ok=True)
VOICES = {"ryan": "en_US-ryan-medium", "lessac": "en_US-lessac-medium", "joe": "en_US-joe-medium"}

LINES = [
    # Written for the final demo footage: a drone flying a small-town main street after the quake - a brick storefront
    # with its front wall down (Building 14), cracked pavement and bricks across Main Street, residents outside damaged
    # houses, then the river bridge (Bridge Street) with its deck dropped into the water. No fire anywhere.
    {"id": "ch3-dispatch-quake", "speaker": "Dispatch", "voice": "lessac", "channel": "ch3",
     "text": "All units, dispatch. Seismic sensor one confirms a magnitude five point eight event, downtown sector three. Drone one is airborne over Main Street. Report damage as you see it."},
    {"id": "ch3-engine7-b14-collapse", "speaker": "Engine 7", "voice": "ryan", "channel": "ch3",
     "text": "Dispatch, Engine 7. Building 14 on Main Street, the brick building at the corner, front wall is down, upper floor collapsed onto the sidewalk. Building 14 collapsed. Nobody visible in the rubble yet."},
    {"id": "ch3-engine7-main-blocked", "speaker": "Engine 7", "voice": "ryan", "channel": "ch3",
     "text": "Dispatch, Engine 7. Main Street is blocked at Building 14, bricks and debris across both lanes and the pavement is cracked open. No vehicles getting through."},
    {"id": "ch3-rescue4-enroute", "speaker": "Rescue 4", "voice": "lessac", "channel": "ch3",
     "text": "Dispatch, Rescue 4, en route to Building 14, E T A two minutes, coming in on Oak Avenue."},
    {"id": "ch3-dispatch-residents", "speaker": "Dispatch", "voice": "lessac", "channel": "ch3",
     "text": "All units, dispatch. Multiple callers on 3rd Street, roofs torn open and walls cracked, residents standing in the street. Building 22 reported damaged, no injuries yet."},
    {"id": "ch3-rescue4-onscene", "speaker": "Rescue 4", "voice": "lessac", "channel": "ch3",
     "text": "Rescue 4 on scene, Building 14. Starting a primary search of the rubble. We have a strong gas odor at the north side, request utility shutoff."},
    {"id": "ch3-ambulance1-bridge-collapsed", "speaker": "Ambulance 1", "voice": "ryan", "channel": "ch3",
     "text": "Dispatch, Ambulance 1. Bridge Street is blocked. The bridge deck over the river has collapsed into the water, the whole center span is gone. Do not send anyone across Bridge Street."},
    {"id": "ch3-dispatch-reroute", "speaker": "Dispatch", "voice": "lessac", "channel": "ch3",
     "text": "Ambulance 2, dispatch. Bridge Street is out and Main Street is blocked. Hold at Lincoln High School staging until we confirm a route to County General."},
    {"id": "ch3-ambulance2-holding", "speaker": "Ambulance 2", "voice": "ryan", "channel": "ch3",
     "text": "Dispatch, Ambulance 2, copy. Holding at Lincoln High School staging, awaiting a route to County General."},
    {"id": "ch3-countygeneral-beds", "speaker": "County General", "voice": "lessac", "channel": "ch3",
     "text": "EOC, County General charge nurse. We are open, twelve beds available, E R accepting walk ins and transports."},
    {"id": "ch3-rescue4-people-trapped", "speaker": "Rescue 4", "voice": "lessac", "channel": "ch3",
     "text": "Dispatch, Rescue 4. We can hear voices under the debris at Building 14, at least two people trapped, starting extrication."},
]


def radio_fx(x: np.ndarray, sr: int, seed: int) -> np.ndarray:
    """Handheld-radio sound: band-limit 300-3400 Hz (FFT brick wall), hard compression, hiss, squelch clicks."""
    rng = np.random.default_rng(seed)
    X = np.fft.rfft(x); f = np.fft.rfftfreq(len(x), 1 / sr)
    X[(f < 250) | (f > 3800)] = 0; y = np.fft.irfft(X, len(x))
    y = np.tanh(y * 2.5) / np.tanh(2.5)                    # compression / slight overdrive (kept mild: whisper drops words when it is harsher)
    y += rng.normal(0, 0.006, len(y))                       # hiss
    pre = np.zeros(int(sr * 0.25)); post = np.zeros(int(sr * 0.3))
    click = lambda: np.concatenate([rng.normal(0, 0.4, int(sr * 0.02)), np.zeros(int(sr * 0.03))])
    y = np.concatenate([pre, click(), y, click(), post])
    return np.clip(y * 0.9, -1, 1)


def main():
    voices = {k: PiperVoice.load(os.path.join(HERE, "voices", f"{v}.onnx")) for k, v in VOICES.items()}
    lib = []
    for i, line in enumerate(LINES):
        v = voices[line["voice"]]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w: v.synthesize_wav(line["text"], w)
        buf.seek(0)
        with wave.open(buf, "rb") as w: sr = w.getframerate(); x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        y = radio_fx(x, sr, seed=i)
        # 16 kHz mono, what the ASR likes
        n = int(len(y) * 16000 / sr); y = np.interp(np.linspace(0, len(y) - 1, n), np.arange(len(y)), y)
        path = os.path.join(OUT, f"{line['id']}.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes((y * 32767).astype(np.int16).tobytes())
        lib.append({**{k: line[k] for k in ("id", "speaker", "channel", "text")}, "file": os.path.basename(path), "seconds": round(n / 16000, 1), "voice": VOICES[line["voice"]]})
        print(f"{line['id']:36s} {n/16000:5.1f}s  {line['speaker']}")
    json.dump(lib, open(os.path.join(OUT, "library.json"), "w"), indent=1)
    print("library.json:", len(lib), "lines")


if __name__ == "__main__":
    main()
