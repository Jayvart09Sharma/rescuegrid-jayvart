"""RescueGrid vision service: two-tier hazard detection.

Tier 1  Every frame, ~16 ms total:
          a) YOLO-World (open-vocabulary detector) -> boxes for people/vehicles/etc.
          b) CLIP ViT-L/14 zero-shot scene head    -> what kind of hazard scene this is
             (YOLO cannot see scene-level concepts like "flooded road"; CLIP can).
Tier 2  Qwen3-VL via the ZRT proxy on KEYFRAMES only (new hazard class seen,
        or KEYFRAME_SEC elapsed for that source), returns a structured assessment.

Every event carries ts + source provenance so it can go straight into Neo4j.
Live and replay adapters both POST frames to /v1/vision/frame; the models never
know which one is feeding them.
"""
import base64, io, json, os, threading, time, uuid, logging
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import FastAPI, File, Form, UploadFile
from PIL import Image

log = logging.getLogger("vision")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---- config -----------------------------------------------------------------
YOLO_WEIGHTS   = os.environ.get("YOLO_WEIGHTS", os.path.join(os.path.dirname(__file__), "weights", "yolov8m-worldv2.pt"))
YOLO_CONF      = float(os.environ.get("YOLO_CONF", "0.20"))
YOLO_IMGSZ     = int(os.environ.get("YOLO_IMGSZ", "640"))
SCENE_MODEL    = os.environ.get("SCENE_MODEL", "ViT-L/14")       # CLIP variant; ViT-B/16 is 3x faster, weaker on flood
SCENE_CONF     = float(os.environ.get("SCENE_CONF", "0.5"))      # min prob to report a non-normal scene as THE label
SCENE_MULTI    = float(os.environ.get("SCENE_MULTI", "0.3"))     # every non-normal scene above this is listed in scene.labels
ZRT_URL        = os.environ.get("ZRT_URL", "http://127.0.0.1:8080/v1")
VLM_MODEL      = os.environ.get("VLM_MODEL", "vision")
VLM_MAX_SIDE   = int(os.environ.get("VLM_MAX_SIDE", "640"))     # resize longest side before sending to the VLM
VLM_MAX_TOKENS = int(os.environ.get("VLM_MAX_TOKENS", "120"))
KEYFRAME_SEC   = float(os.environ.get("KEYFRAME_SEC", "10"))
EVENT_SINK_URL = os.environ.get("EVENT_SINK_URL")                # optional webhook (Shresth's event bus)
FRAME_DIR      = os.environ.get("FRAME_DIR", os.path.join(os.path.dirname(__file__), "frames"))  # keyframes saved here for provenance

HAZARD_CLASSES = [c.strip() for c in os.environ.get("HAZARD_CLASSES",
    "collapsed building,damaged building,rubble,flooded road,flood water,fire,smoke,"
    "fallen tree,downed power line,landslide,debris on road,crashed car,car,truck,bus,"
    "person,ambulance,fire truck,police car,boat,helicopter").split(",")]

# Scene classes: key -> prompt ensemble (mean text embedding). Edit freely; no retraining.
SCENES = {
    "collapsed_building": ["a collapsed building with rubble after an earthquake", "a multi-storey building with its upper floors collapsed, exposed rooms and bent rebar", "a partially collapsed office building with a large concrete slab fallen against its wall"],
    "flooded_road":       ["a flooded road with water covering the street", "a street submerged in brown flood water", "people wading through flood water on a road"],
    "wildfire_smoke":     ["a wildfire with smoke near a road", "thick smoke from a wildfire over hills"],
    "structure_fire":     ["a burning building with flames"],
    "landslide":          ["a landslide of mud and rock covering a road"],
    "fallen_tree":        ["a fallen tree blocking a road"],
    "vehicle_crash":      ["a car crash with damaged vehicles on the road"],
    "blocked_road":       ["cars stopped behind broken concrete and bricks scattered across the road surface", "a street physically blocked by fallen debris with a barrier of rubble across all lanes, buildings intact", "a pickup truck halted at a pile of debris lying across the pavement at an intersection"],
    "normal":             ["normal traffic on a highway", "an intact street with no damage", "a clear road with cars driving normally", "bumper-to-bumper traffic congestion on a road with nothing blocking it", "a busy multi-lane road full of slow-moving cars, no debris"],
}

# ---- state ------------------------------------------------------------------
app = FastAPI(title="RescueGrid Vision")
EVENTS: deque = deque(maxlen=5000)
LAST_SEEN: dict = {}          # source_id -> {"labels": set, "keyframe_ts": float}
STATS = {"frames": 0, "tier1_ms": deque(maxlen=500), "tier2_ms": deque(maxlen=200), "vlm_calls": 0, "vlm_errors": 0, "vlm_skipped_busy": 0}
VLM_POOL = ThreadPoolExecutor(max_workers=1)   # one VLM call at a time so we never queue up behind ourselves
VLM_BUSY = threading.Semaphore(1)
LOCK = threading.Lock()
MODEL = None
CLIP = {}   # model, preprocess, text features, keys

os.makedirs(FRAME_DIR, exist_ok=True)

def now_iso(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

# ---- tier 1: YOLO-World -----------------------------------------------------
@app.on_event("startup")
def load_model():
    global MODEL
    from ultralytics import YOLOWorld
    t0 = time.time()
    MODEL = YOLOWorld(YOLO_WEIGHTS)
    MODEL.set_classes(HAZARD_CLASSES)
    # warm-up so the first real frame is not the slow one
    MODEL.predict(Image.new("RGB", (YOLO_IMGSZ, YOLO_IMGSZ)), imgsz=YOLO_IMGSZ, conf=YOLO_CONF, verbose=False)
    log.info("YOLO-World loaded (%d classes) in %.1fs", len(HAZARD_CLASSES), time.time() - t0)
    import clip, torch
    t0 = time.time()
    model, pre = clip.load(SCENE_MODEL, device="cuda"); model.eval()
    with torch.no_grad():
        feats = []
        for k in SCENES:
            e = model.encode_text(clip.tokenize(SCENES[k]).to("cuda")); e = e / e.norm(dim=-1, keepdim=True); e = e.mean(0); feats.append(e / e.norm())
        CLIP.update(model=model, pre=pre, text=torch.stack(feats), keys=list(SCENES), torch=torch)
    scene_head(Image.new("RGB", (224, 224)))
    log.info("CLIP %s scene head loaded (%d scenes) in %.1fs", SCENE_MODEL, len(SCENES), time.time() - t0)

def scene_head(img: Image.Image):
    """Zero-shot scene classification of the whole frame. Returns (label, conf, top3, ms)."""
    torch = CLIP["torch"]; t0 = time.perf_counter()
    with torch.no_grad():
        x = CLIP["pre"](img).unsqueeze(0).to("cuda")
        f = CLIP["model"].encode_image(x); f = f / f.norm(dim=-1, keepdim=True)
        p = (100 * f @ CLIP["text"].T).softmax(-1)[0].tolist()
    ms = (time.perf_counter() - t0) * 1000
    ranked = sorted(zip(CLIP["keys"], p), key=lambda x: -x[1])
    label, conf = ranked[0]
    if label != "normal" and conf < SCENE_CONF: label = "uncertain"
    # A real scene can be two things at once (a collapse whose rubble also blocks the street): keep every
    # non-normal scene above SCENE_MULTI so the event carries both instead of a coin-flip between them.
    multi = [(k, round(v, 3)) for k, v in ranked if v >= SCENE_MULTI and k != "normal"]
    return label, round(conf, 3), [(k, round(v, 3)) for k, v in ranked[:3]], ms, multi

def tier1(img: Image.Image):
    t0 = time.perf_counter()
    res = MODEL.predict(img, imgsz=YOLO_IMGSZ, conf=YOLO_CONF, verbose=False)[0]
    ms = (time.perf_counter() - t0) * 1000
    dets = []
    for b in res.boxes:
        dets.append({"label": res.names[int(b.cls)], "conf": round(float(b.conf), 3),
                     "bbox_xyxy": [round(v) for v in b.xyxy[0].tolist()]})
    return dets, ms

# ---- tier 2: Qwen3-VL via ZRT proxy -----------------------------------------
# Compact schema (short keys, no whitespace) so the VLM spends ~40 output tokens instead of ~85.
# Keys are expanded to readable names in the event.
ASSESS_SCHEMA = {
    "type": "object",
    "properties": {
        "d":   {"type": "string", "maxLength": 160},                     # description, one sentence
        "h":   {"type": "array", "items": {"type": "object", "properties": {
                   "t": {"type": "string"}, "s": {"type": "integer", "minimum": 1, "maximum": 5},
                   "c": {"type": "number", "minimum": 0, "maximum": 1}}, "required": ["t", "s", "c"]}},
        "p":   {"type": ["boolean", "null"]},                            # road passable
        "ppl": {"type": "boolean"},                                      # people visible
    },
    "required": ["d", "h", "p", "ppl"],
}
VLM_SYSTEM = ("You are the vision analyst for a county EOC watch officer. Describe only what is visible, in EOC vocabulary. "
              "Reply with ONE line of compact JSON, no spaces or newlines, exactly these keys: "
              '{"d":"<one sentence, max 25 words>","h":[{"t":"<hazard>","s":<severity 1-5>,"c":<confidence 0-1>}],'
              '"p":<true|false|null road passable>,"ppl":<true|false people visible>}. h is [] if no real hazard.')

def expand(a: dict) -> dict:
    return {"description": a["d"], "hazards": [{"type": h["t"], "severity": h["s"], "confidence": h["c"]} for h in a["h"]],
            "road_passable": a["p"], "people_visible": a["ppl"]}

def to_data_uri(img: Image.Image) -> str:
    im = img.copy(); im.thumbnail((VLM_MAX_SIDE, VLM_MAX_SIDE))
    buf = io.BytesIO(); im.convert("RGB").save(buf, "JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

def tier2(img: Image.Image, source: dict, dets: list, scene: str, event_id: str, frame_ref: str):
    t0 = time.perf_counter()
    hint = f"scene={scene}; objects=" + (", ".join(sorted({d["label"] for d in dets})) or "none")
    body = {
        "model": VLM_MODEL, "temperature": 0, "max_tokens": VLM_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": VLM_SYSTEM},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": to_data_uri(img)}},
                {"type": "text", "text": f"Source: {source['type']} {source['id']}. Detector flagged: {hint}. Assess the scene."}]}],
        # vLLM structured outputs with whitespace disabled: guaranteed-valid JSON at minimum token cost
        "structured_outputs": {"json": ASSESS_SCHEMA, "disable_any_whitespace": True},
    }
    try:
        r = httpx.post(f"{ZRT_URL}/chat/completions", json=body, timeout=120)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        assessment = expand(json.loads(content))
        err = None
    except Exception as e:  # keep the pipeline alive; the tier-1 event already exists
        assessment, err = None, str(e)[:300]
        STATS["vlm_errors"] += 1
    ms = (time.perf_counter() - t0) * 1000
    STATS["tier2_ms"].append(ms); STATS["vlm_calls"] += 1
    ev = {"event_id": str(uuid.uuid4()), "parent_event_id": event_id, "ts": now_iso(), "source": source,
          "tier": 2, "model": f"{VLM_MODEL} (Qwen3-VL via ZRT)", "latency_ms": round(ms), "frame_ref": frame_ref,
          "assessment": assessment, "error": err}
    emit(ev)
    log.info("tier2 %s %s %.0fms %s", source["id"], "ok" if assessment else "ERR", ms, (assessment or {}).get("description", err)[:80])

def tier2_guarded(*a):
    if not VLM_BUSY.acquire(blocking=False):
        STATS["vlm_skipped_busy"] += 1; return
    try: tier2(*a)
    finally: VLM_BUSY.release()

# ---- events -----------------------------------------------------------------
def emit(ev: dict):
    with LOCK: EVENTS.append(ev)
    if EVENT_SINK_URL:
        try: httpx.post(EVENT_SINK_URL, json=ev, timeout=5)
        except Exception as e: log.warning("event sink failed: %s", e)

def should_keyframe(source_id: str, labels: set, scene: str, force: bool):
    st = LAST_SEEN.setdefault(source_id, {"labels": set(), "scene": None, "keyframe_ts": 0.0})
    new = labels - st["labels"]
    scene_changed = scene not in (st["scene"], "uncertain")
    due = (time.time() - st["keyframe_ts"]) >= KEYFRAME_SEC
    st["labels"] = labels; st["scene"] = scene
    reason = ("forced" if force else f"scene:{scene}" if scene_changed else
              "new:" + ",".join(sorted(new)) if new else "interval" if due else None)
    if reason: st["keyframe_ts"] = time.time()
    return reason

# ---- API --------------------------------------------------------------------
@app.get("/health")
def health(): return {"status": "ok", "tier1": ["yolo-world", f"clip-{SCENE_MODEL}"], "tier2": VLM_MODEL,
                      "object_classes": HAZARD_CLASSES, "scene_classes": list(SCENES)}

@app.post("/v1/vision/frame")
async def frame(file: UploadFile = File(...), source_id: str = Form("drone-1"), source_type: str = Form("drone"),
                ts: Optional[str] = Form(None), frame_ref: Optional[str] = Form(None), force_vlm: bool = Form(False)):
    raw = await file.read()
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    dets, ms_det = tier1(img)
    scene, scene_conf, scene_top3, ms_scene, scene_multi = scene_head(img)
    ms = ms_det + ms_scene
    STATS["frames"] += 1; STATS["tier1_ms"].append(ms)
    labels = {d["label"] for d in dets}
    source = {"type": source_type, "id": source_id}
    event_id = str(uuid.uuid4())
    reason = should_keyframe(source_id, labels | {"scene:" + k for k, _ in scene_multi}, scene, force_vlm)
    if reason and frame_ref is None:  # save the keyframe so provenance can show the actual image
        frame_ref = os.path.join(FRAME_DIR, f"{source_id}_{int(time.time()*1000)}.jpg")
        img.save(frame_ref, "JPEG", quality=85)
    ev = {"event_id": event_id, "ts": ts or now_iso(), "source": source, "tier": 1,
          "model": f"yolov8m-worldv2 + clip-{SCENE_MODEL}", "latency_ms": round(ms, 1),
          "latency_breakdown_ms": {"detector": round(ms_det, 1), "scene": round(ms_scene, 1)},
          "frame_ref": frame_ref, "detections": dets,
          "scene": {"label": scene, "conf": scene_conf, "labels": scene_multi, "top3": scene_top3}, "keyframe_reason": reason}
    emit(ev)
    if reason: VLM_POOL.submit(tier2_guarded, img, source, dets, scene, event_id, frame_ref)
    return ev

@app.get("/v1/vision/events")
def events(since: Optional[str] = None, limit: int = 200, tier: Optional[int] = None):
    with LOCK: evs = list(EVENTS)
    if since: evs = [e for e in evs if e["ts"] > since]
    if tier:  evs = [e for e in evs if e["tier"] == tier]
    return {"events": evs[-limit:]}

@app.get("/v1/vision/stats")
def stats():
    def summ(d):
        if not d: return None
        s = sorted(d); return {"n": len(s), "p50_ms": round(s[len(s)//2], 1), "p95_ms": round(s[int(len(s)*0.95)], 1), "mean_ms": round(sum(s)/len(s), 1)}
    return {"frames": STATS["frames"], "tier1": summ(STATS["tier1_ms"]), "tier2": summ(STATS["tier2_ms"]),
            "vlm_calls": STATS["vlm_calls"], "vlm_errors": STATS["vlm_errors"], "vlm_skipped_busy": STATS["vlm_skipped_busy"]}
