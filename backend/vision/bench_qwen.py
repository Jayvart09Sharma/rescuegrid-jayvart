"""Benchmark Qwen3-VL-8B (ZRT 'vision') on the team's drone clips: how should a clip be sent to the VLM?
Usage: python bench_qwen.py results.jsonl   (env RUNS=3, MODES=frame1,multi4,..., HINT=1)

Modes (all ask for the same compact assessment schema the vision service uses in production):
  frame1      one keyframe (middle of clip), 640px            <- today's tier-2 path
  seq1fps     one frame per second, one request each, serial   <- "VLM on every second"
  multiK      K frames evenly spaced, one request, 640px
  video_*     the clip as native video input (video_url, base64 mp4)

Every run perturbs one pixel so vLLM's multimodal/prefix caches can't hit: numbers reflect fresh footage.
"""
import base64, io, json, os, sys, tempfile, time, uuid
import cv2, httpx, numpy as np
from PIL import Image

ZRT = os.environ.get("ZRT_URL", "http://127.0.0.1:8080/v1")
OUT = sys.argv[1] if len(sys.argv) > 1 else "results.jsonl"
RUNS = int(os.environ.get("RUNS", "3"))
_V = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "videos")
CLIPS = {"clip3_building14": os.path.join(_V, "pixverse_c1 (3).mp4"),
         "clip4_mainstreet": os.path.join(_V, "pixverse_c1 (4).mp4")}

# --- same schema + system prompt as Shresth/rescuegrid/vision/server.py (tier 2) ---
SCHEMA = {"type": "object", "properties": {
    "d": {"type": "string", "maxLength": 160},
    "h": {"type": "array", "items": {"type": "object", "properties": {
        "t": {"type": "string"}, "s": {"type": "integer", "minimum": 1, "maximum": 5},
        "c": {"type": "number", "minimum": 0, "maximum": 1}}, "required": ["t", "s", "c"]}},
    "p": {"type": ["boolean", "null"]}, "ppl": {"type": "boolean"}},
    "required": ["d", "h", "p", "ppl"]}
SYSTEM = ("You are the vision analyst for a county EOC watch officer. Describe only what is visible, in EOC vocabulary. "
          "Reply with ONE line of compact JSON, no spaces or newlines, exactly these keys: "
          '{"d":"<one sentence, max 25 words>","h":[{"t":"<hazard>","s":<severity 1-5>,"c":<confidence 0-1>}],'
          '"p":<true|false|null road passable>,"ppl":<true|false people visible>}. h is [] if no real hazard.')


def read_frames(path):
    cap = cv2.VideoCapture(path); fps = cap.get(cv2.CAP_PROP_FPS); fr = []
    while True:
        ok, f = cap.read()
        if not ok: break
        fr.append(f)
    return fr, fps


def poke(bgr):  # 1-pixel change -> new mm hash, visually identical
    b = bgr.copy(); b[0, 0] = np.random.randint(0, 255, 3); return b


def jpeg_uri(bgr, max_side=640):
    im = Image.fromarray(cv2.cvtColor(poke(bgr), cv2.COLOR_BGR2RGB)); im.thumbnail((max_side, max_side))
    buf = io.BytesIO(); im.save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode(), len(buf.getvalue())


def mp4_uri(frames, fps, max_side):
    h, w = frames[0].shape[:2]; s = min(1.0, max_side / max(h, w)); W, H = int(w * s) // 2 * 2, int(h * s) // 2 * 2
    tmp = os.path.join(tempfile.gettempdir(), f"_v{uuid.uuid4().hex[:6]}.mp4")
    vw = cv2.VideoWriter(tmp, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    for i, f in enumerate(frames):
        vw.write(poke(cv2.resize(f, (W, H))) if i == 0 else cv2.resize(f, (W, H)))
    vw.release(); data = open(tmp, "rb").read(); os.remove(tmp)
    return "data:video/mp4;base64," + base64.b64encode(data).decode(), len(data)


def call(content, extra=None, max_tokens=120):
    body = {"model": "vision", "temperature": 0, "max_tokens": max_tokens, "stream": True,
            "stream_options": {"include_usage": True},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content}],
            "structured_outputs": {"json": SCHEMA, "disable_any_whitespace": True}}
    if extra: body.update(extra)
    t0 = time.perf_counter(); ttft = None; text = ""; usage = {}
    with httpx.stream("POST", f"{ZRT}/chat/completions", json=body, timeout=300) as r:
        if r.status_code != 200:
            return {"error": f"{r.status_code} {r.read().decode()[:400]}", "total_s": time.perf_counter() - t0}
        for line in r.iter_lines():
            if not line.startswith("data: ") or line == "data: [DONE]": continue
            j = json.loads(line[6:])
            if j.get("usage"): usage = j["usage"]
            for ch in j.get("choices", []):
                d = ch.get("delta", {}).get("content")
                if d:
                    if ttft is None: ttft = time.perf_counter() - t0
                    text += d
    tot = time.perf_counter() - t0
    ct = usage.get("completion_tokens", 0)
    try: parsed = json.loads(text)
    except Exception: parsed = None
    return {"total_s": round(tot, 3), "ttft_s": round(ttft or tot, 3), "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": ct, "decode_tok_s": round((ct - 1) / (tot - ttft), 1) if ttft and ct > 1 and tot > ttft else None,
            "output": parsed if parsed is not None else text}


HINTS = {"clip3_building14": "scene=collapsed_building; objects=car, fire truck, person, truck",
         "clip4_mainstreet": "scene=blocked_road; objects=car, person, truck"}
CUR = {"clip": None}

def txt(clip_s, n=None, video=False):
    what = f"a {clip_s:.0f}-second drone video clip" if video else (f"{n} frames sampled in order from a {clip_s:.0f}-second drone clip" if n else "a drone frame")
    hint = f" Detector flagged: {HINTS[CUR['clip']]}." if os.environ.get("HINT") else ""
    return {"type": "text", "text": f"Source: drone drone-1. Input: {what}.{hint} Assess the scene."}


def modes(frames, fps):
    n = len(frames); dur = n / fps
    def pick(k): return [frames[round(i * (n - 1) / (k - 1))] for i in range(k)] if k > 1 else [frames[n // 2]]

    def frame1():
        t = time.perf_counter(); uri, b = jpeg_uri(frames[n // 2]); prep = time.perf_counter() - t
        r = call([{"type": "image_url", "image_url": {"url": uri}}, txt(dur)]); r["prep_s"] = round(prep, 3); r["upload_bytes"] = b; return r

    def seq1fps():
        idx = [min(n - 1, round(s * fps)) for s in range(int(dur) + 1)][:int(round(dur))]
        t0 = time.perf_counter(); per = []; prep = 0
        for i in idx:
            t = time.perf_counter(); uri, _ = jpeg_uri(frames[i]); prep += time.perf_counter() - t
            per.append(call([{"type": "image_url", "image_url": {"url": uri}}, txt(dur)]))
        return {"total_s": round(time.perf_counter() - t0, 3), "prep_s": round(prep, 3), "requests": len(per),
                "per_request_s": [p["total_s"] for p in per], "prompt_tokens": sum(p["prompt_tokens"] or 0 for p in per),
                "completion_tokens": sum(p["completion_tokens"] for p in per), "output": [p["output"] for p in per]}

    def multi(k, side=640):
        def f():
            t = time.perf_counter(); uris = [jpeg_uri(x, side) for x in pick(k)]; prep = time.perf_counter() - t
            c = [{"type": "image_url", "image_url": {"url": u}} for u, _ in uris] + [txt(dur, k)]
            r = call(c); r["prep_s"] = round(prep, 3); r["upload_bytes"] = sum(b for _, b in uris); return r
        return f

    def video(side, vfps):
        def f():
            t = time.perf_counter(); uri, b = mp4_uri(frames, fps, side); prep = time.perf_counter() - t
            r = call([{"type": "video_url", "video_url": {"url": uri}}, txt(dur, video=True)],
                     extra={"mm_processor_kwargs": {"fps": vfps}})
            r["prep_s"] = round(prep, 3); r["upload_bytes"] = b; return r
        return f

    return {"frame1": frame1, "seq1fps": seq1fps, "multi4": multi(4), "multi8": multi(8),
            "video_360p_2fps": video(640, 2), "video_720p_2fps": video(1280, 2), "video_360p_4fps": video(640, 4)}


if __name__ == "__main__":
    only = os.environ.get("MODES", "").split(",") if os.environ.get("MODES") else None
    with open(OUT, "a") as fo:
        for clip, path in CLIPS.items():
            CUR["clip"] = clip; t = time.perf_counter(); frames, fps = read_frames(path); decode_s = time.perf_counter() - t
            for name, fn in modes(frames, fps).items():
                if only and name not in only: continue
                for run in range(RUNS + 1):          # run 0 = warm-up, discarded in the summary
                    r = fn(); r.update(clip=clip, mode=name + ("+hint" if os.environ.get("HINT") else ""), run=run, decode_clip_s=round(decode_s, 3))
                    fo.write(json.dumps(r) + "\n"); fo.flush()
                    print(f"{clip:18s} {name:16s} run{run} total={r.get('total_s')}s ttft={r.get('ttft_s')} "
                          f"ptok={r.get('prompt_tokens')} ctok={r.get('completion_tokens')} "
                          f"{json.dumps(r.get('output', r.get('error')))[:170]}", flush=True)
