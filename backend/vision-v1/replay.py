"""Replay adapter: plays a folder of images or a video file into the vision service on a timer.
Same interface a live RTSP adapter would use. Usage:
  python replay.py samples/ --source-id drone-1 --fps 2
  python replay.py footage.mp4 --source-id roadcam-3 --source-type roadcam --fps 4
"""
import argparse, glob, io, os, sys, time, httpx
p = argparse.ArgumentParser()
p.add_argument("path"); p.add_argument("--url", default="http://127.0.0.1:8091")
p.add_argument("--source-id", default="drone-1"); p.add_argument("--source-type", default="drone")
p.add_argument("--fps", type=float, default=2.0); p.add_argument("--loop", action="store_true")
a = p.parse_args()

def frames():
    if os.path.isdir(a.path):
        for f in sorted(glob.glob(os.path.join(a.path, "*.[jJpP][pPnN][gG]"))):
            yield os.path.basename(f), open(f, "rb").read()
    else:
        import cv2
        cap = cv2.VideoCapture(a.path); src_fps = cap.get(cv2.CAP_PROP_FPS) or 30; step = max(1, round(src_fps / a.fps)); i = 0
        while True:
            ok, fr = cap.read()
            if not ok: break
            if i % step == 0:
                ok, buf = cv2.imencode(".jpg", fr, [cv2.IMWRITE_JPEG_QUALITY, 85]); yield f"frame{i:06d}.jpg", buf.tobytes()
            i += 1

while True:
    for name, data in frames():
        t0 = time.time()
        r = httpx.post(f"{a.url}/v1/vision/frame", files={"file": (name, data, "image/jpeg")},
                       data={"source_id": a.source_id, "source_type": a.source_type, "frame_ref": name}, timeout=60).json()
        labels = ",".join(sorted({d["label"] for d in r["detections"]})) or "-"; scene = f"{r['scene']['label']}:{r['scene']['conf']:.2f}"
        print(f"{name:28s} tier1={r['latency_ms']:6.1f}ms rtt={(time.time()-t0)*1000:6.0f}ms scene={scene} dets={len(r["detections"])} [{labels}] keyframe={r['keyframe_reason']}", flush=True)
        time.sleep(max(0, 1 / a.fps - (time.time() - t0)))
    if not a.loop: break
