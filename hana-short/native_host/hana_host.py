"""Chrome Native Messaging host "com.hanamedia.short".

Chrome starts this process (via hana_host.bat) when the extension calls chrome.runtime.connectNative().
Protocol: 4-byte little-endian length + UTF-8 JSON, both directions. Every request carries "id"; the reply
echoes it. All state lives in the job folders (hana.flow), so the host itself is stateless and safe to restart.
"""
from __future__ import annotations

import json
import os
import struct
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from hana import __version__, flow  # noqa: E402
from hana.config import jobs_dir, load_config, resolve_path  # noqa: E402
from hana.jobs import now_iso  # noqa: E402

MAX_OUT = 1024 * 1024 - 16  # Chrome limit for host -> extension messages is 1 MB


def read_msg(stream) -> dict | None:
    raw = stream.read(4)
    if len(raw) < 4:
        return None
    (n,) = struct.unpack("<I", raw)
    return json.loads(stream.read(n).decode("utf-8"))


def write_msg(stream, obj: dict) -> None:
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_OUT:
        obj = {"id": obj.get("id"), "ok": False, "error": f"reply too large ({len(data)} bytes) - ảnh tham chiếu quá lớn?"}
        data = json.dumps(obj).encode("utf-8")
    stream.write(struct.pack("<I", len(data)))
    stream.write(data)
    stream.flush()


def log(cfg, *parts) -> None:
    try:
        with open(jobs_dir(cfg) / "_host.log", "a", encoding="utf-8") as f:
            f.write(now_iso() + " " + " ".join(str(p) for p in parts) + "\n")
    except OSError:
        pass


def handle(cfg: dict, msg: dict) -> dict:
    cmd = msg.get("cmd")
    if cmd == "hello":
        changed = flow.recover_orphans(cfg, msg.get("active_token"))
        dl = resolve_path(cfg["downloads_dir"])
        return {"ok": True, "version": __version__, "recovered": changed, "downloads_dir": str(dl),
                "blocked": flow.global_block(cfg), "flow": {k: cfg["flow"][k] for k in ("base_url", "project_url")}}
    if cmd == "claim_task":
        return {"ok": True, **flow.claim_task(cfg, msg.get("job"))}
    if cmd == "pre_submit":
        return flow.pre_submit(cfg, msg["token"])
    if cmd == "submitted":
        return flow.submitted(cfg, msg["token"], msg.get("evidence") or {}, msg.get("project_url", ""))
    if cmd == "submit_unknown":
        return flow.submit_unknown(cfg, msg["token"], msg.get("detail", ""))
    if cmd == "blocked":
        return flow.blocked(cfg, msg.get("reason", "unknown"), msg.get("detail", ""), msg.get("token"))
    if cmd == "gen_failed":
        return flow.gen_failed(cfg, msg["token"], msg.get("detail", ""))
    if cmd == "downloaded":
        return flow.downloaded(cfg, msg["token"], msg["path"])
    if cmd == "status":
        return {"ok": True, "blocked": flow.global_block(cfg)}
    if cmd == "ui_report":
        out = jobs_dir(cfg) / "_flow_ui_report.json"
        out.write_text(json.dumps(msg.get("report"), ensure_ascii=False, indent=2), encoding="utf-8")
        return {"ok": True, "saved": str(out)}
    if cmd == "log":
        log(cfg, "EXT", msg.get("level", "info"), msg.get("text", ""))
        return {"ok": True}
    return {"ok": False, "error": f"unknown cmd {cmd}"}


def main() -> int:
    if sys.platform == "win32":
        import msvcrt
        msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
        msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
    stdin, stdout = sys.stdin.buffer, sys.stdout.buffer
    cfg = load_config()
    log(cfg, "host start", sys.argv[1:])
    while True:
        try:
            msg = read_msg(stdin)
        except Exception as e:  # malformed frame
            log(cfg, "read error", e)
            return 1
        if msg is None:
            log(cfg, "host exit (stdin closed)")
            return 0
        try:
            cfg = load_config()  # pick up config edits without restarting Chrome
            reply = handle(cfg, msg)
        except Exception as e:
            log(cfg, "ERROR", msg.get("cmd"), traceback.format_exc())
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        reply["id"] = msg.get("id")
        if msg.get("cmd") != "claim_task" or reply.get("task"):
            log(cfg, "<-", msg.get("cmd"), "->", json.dumps({k: v for k, v in reply.items() if k != "task"},
                                                            ensure_ascii=False)[:300])
        write_msg(stdout, reply)


if __name__ == "__main__":
    sys.exit(main())
