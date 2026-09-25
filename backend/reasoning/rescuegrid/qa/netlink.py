"""Application-layer WAN emulation for the LLM client, used to benchmark a cloud endpoint under a degraded link.

Jayvant's network simulator (Jaya/jayvart/network/network_simulator.py) only sleeps on its own /cloud/test route and
does not shape traffic, and shaping with tc/netem needs root, so the benchmark emulates the link in the HTTP
transport of the model client instead. Parameters follow the numbers the twin already displays (bus/gateway.py
/netsim/status): normal = measured real link; throttled = 256 kbps with latency 256 / max(64, kbps) * 2600 ms.

  LLM_NET_PROFILE = none | throttled | disconnected      (none = the real network, untouched)
  LLM_NET_KBPS    = link rate for 'throttled' (default 256)
  LLM_NET_RTT_MS  = extra round-trip latency for 'throttled' (default: the gateway formula, 2600 ms at 256 kbps)

Per request the transport adds: RTT + (request bytes + response bytes) * 8 / (kbps * 1000) seconds. The delay is
recorded on the response so the benchmark can separate emulated link time from real network + inference time.
This is an emulation, stated as such in every results file; it models latency and bandwidth, not loss/jitter."""
from __future__ import annotations

import os
import time

import httpx2


class LinkDown(httpx2.ConnectError):
    pass


def profile_from_env() -> dict:
    prof = os.environ.get("LLM_NET_PROFILE", "none").strip().lower() or "none"
    kbps = float(os.environ.get("LLM_NET_KBPS", "256"))
    rtt = os.environ.get("LLM_NET_RTT_MS")
    rtt_ms = float(rtt) if rtt else 256 / max(64.0, kbps) * 2600.0
    return {"profile": prof, "kbps": kbps if prof == "throttled" else None, "rtt_ms": rtt_ms if prof == "throttled" else None}


class EmulatedLink(httpx2.BaseTransport):
    def __init__(self, profile: dict, inner: httpx2.BaseTransport | None = None):
        self.p = profile
        self.inner = inner or httpx2.HTTPTransport(retries=0)
        self.emulated_seconds = 0.0   # cumulative, read by the benchmark

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        if self.p["profile"] == "disconnected":
            raise LinkDown("WAN link down (emulated)", request=request)
        resp = self.inner.handle_request(request)
        if self.p["profile"] != "throttled":
            return resp
        resp.read()
        req_bytes = len(request.content or b"")
        resp_bytes = len(resp.content or b"")
        delay = self.p["rtt_ms"] / 1000.0 + (req_bytes + resp_bytes) * 8 / (self.p["kbps"] * 1000.0)
        time.sleep(delay)
        self.emulated_seconds += delay
        resp.extensions["emulated_link_seconds"] = delay
        return resp

    def close(self) -> None:
        self.inner.close()


def http_client_for(profile: dict, timeout: float):
    """An httpx2 client for the OpenAI SDK, or None to use the SDK default (real network)."""
    if profile["profile"] == "none":
        return None, None
    from openai import DefaultHttpxClient
    transport = EmulatedLink(profile)
    return DefaultHttpxClient(transport=transport, timeout=timeout), transport
