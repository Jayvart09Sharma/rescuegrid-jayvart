#!/usr/bin/env bash
# One line per RescueGrid service.
for spec in "7688 Neo4j (bolt)" "8080 ZRT proxy (llm + vision models)" "8090 ASR faster-whisper" "8091 vision v2" "8095 Q&A (Kenil)" "8096 event bus + fusion" "8097 twin gateway + console"; do
  p=${spec%% *}; n=${spec#* }
  if ss -ltn 2>/dev/null | grep -q ":$p "; then
    path=/health; [ "$p" = 8080 ] && path=/v1/models
    h=$(curl -s -m 3 "http://127.0.0.1:$p$path" 2>/dev/null | head -c 90)
    printf '  %-5s %-34s UP   %s\n' ":$p" "$n" "${h:-}"
  else
    printf '  %-5s %-34s DOWN\n' ":$p" "$n"
  fi
done
command -v zrt >/dev/null 2>&1 && (zrt status 2>/dev/null | grep -E "llm|vision" | sed 's/^/  /') || true
