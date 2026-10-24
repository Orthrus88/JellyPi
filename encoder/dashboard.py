#!/usr/bin/env python3
import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from compatibility import EXTENSIONS, describe_plan, probe, transcode_plan

ROOTS = [Path(value) for value in os.getenv("MEDIA_ROOTS", "/media/Movies:/media/TV").split(":")]
STATE_DIR = Path(os.getenv("STATE_DIR", "/media/.jellypi-encoder"))
STATUS = STATE_DIR / "status.json"
MIN_AGE = int(os.getenv("MIN_AGE_SECONDS", "900"))
PORT = int(os.getenv("DASHBOARD_PORT", "8097"))
WORKER_OFFLINE_SECONDS = int(os.getenv("WORKER_OFFLINE_SECONDS", "300"))
CACHE = {"updated": None, "scanning": True, "items": [], "error": None}
LOCK = threading.Lock()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def storage() -> list[dict]:
    try:
        usage = shutil.disk_usage(ROOTS[0].parent)
        return [
            {
                "name": "Media storage",
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
                "percent": round(usage.used * 100 / usage.total, 1),
            }
        ]
    except Exception as error:
        return [{"name": "Media storage", "error": str(error)}]


def file_info(path: Path) -> dict:
    stat = path.stat()
    return {"file": str(path), "name": path.name, "size": stat.st_size}


def scan() -> None:
    timestamp = time.time()
    waiting: list[dict] = []
    ready_paths: list[tuple[float, Path]] = []
    for root in ROOTS:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            try:
                if (
                    not path.is_file()
                    or path.suffix.lower() not in EXTENSIONS
                    or path.name.startswith(".")
                    or ".encoder-part" in path.name
                ):
                    continue
                modified = path.stat().st_mtime
                if modified >= timestamp - MIN_AGE:
                    item = file_info(path)
                    item.update(
                        {
                            "state": "waiting",
                            "decision": "Waiting for import safety window",
                            "readyAt": datetime.fromtimestamp(
                                modified + MIN_AGE, timezone.utc
                            ).isoformat(),
                        }
                    )
                    waiting.append(item)
                else:
                    ready_paths.append((modified, path))
            except OSError:
                pass

    ready: list[dict] = []
    for _, path in sorted(ready_paths, key=lambda item: item[0]):
        try:
            action = transcode_plan(probe(path))
            if action:
                item = file_info(path)
                item.update(
                    {"state": "ready", "decision": describe_plan(action)}
                )
                ready.append(item)
        except Exception as error:
            ready.append(
                {
                    "file": str(path),
                    "name": path.name,
                    "state": "error",
                    "decision": "Could not inspect",
                    "error": str(error),
                }
            )
    with LOCK:
        CACHE.update(
            {
                "updated": now(),
                "scanning": False,
                "items": ready + sorted(waiting, key=lambda item: item["readyAt"]),
                "error": None,
            }
        )


def scan_loop() -> None:
    while True:
        try:
            with LOCK:
                CACHE["scanning"] = True
            scan()
        except Exception as error:
            with LOCK:
                CACHE.update({"updated": now(), "scanning": False, "error": str(error)})
        time.sleep(120)


def overview() -> dict:
    status_files = sorted(STATE_DIR.glob("status*.json"))
    if STATUS not in status_files:
        status_files.insert(0, STATUS)
    workers = [worker for path in status_files if (worker := read_json(path))]
    for worker in workers:
        try:
            updated = datetime.fromisoformat(worker["updated"].replace("Z", "+00:00"))
            if (datetime.now(timezone.utc) - updated).total_seconds() > WORKER_OFFLINE_SECONDS:
                worker["state"] = "offline"
                worker["progress"] = 0
                worker["file"] = None
        except (KeyError, TypeError, ValueError):
            worker["state"] = "offline"
    with LOCK:
        queue = dict(CACHE)
        items = [dict(item) for item in CACHE["items"]]
    active_files = set()
    active_items = []
    for worker in workers:
        if worker.get("state") in {"encoding", "validating"} and worker.get("file"):
            active_files.add(worker["file"])
            active_items.append(
                {
                    "name": Path(worker["file"]).name,
                    "file": worker["file"],
                    "state": worker["state"],
                    "progress": worker.get("progress", 0),
                    "worker": worker.get("worker", "encoder"),
                    "decision": worker.get("decision"),
                }
            )
    items = active_items + [item for item in items if item.get("file") not in active_files]
    queue["items"] = items
    queue["count"] = len(items)
    return {
        "worker": workers[0] if workers else {},
        "workers": workers,
        "storage": storage(),
        "queue": queue,
    }


HTML = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>JellyPi Encoder</title><style>
:root{color-scheme:dark}*{box-sizing:border-box}body{font-family:system-ui;background:#0d131b;color:#e8edf5;margin:auto;padding:24px;max-width:1300px}h1{margin:0 0 4px}h2{font-size:20px;margin:28px 0 12px}.muted,.meta{color:#8fa2b8}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:16px}.card,.queue{background:#19222e;border:1px solid #2c3a4d;border-radius:14px}.card{padding:18px}.name{font-size:19px;font-weight:700}.state,.badge{display:inline-block;margin:8px 0;padding:4px 9px;border-radius:999px;background:#314257;text-transform:capitalize}progress{width:100%;height:18px}.file{word-break:break-word;color:#b9c7d8;margin-top:10px}.meta{font-size:13px;margin-top:8px}.error{color:#ff9292}.ok{color:#7ee2a8}.row{display:flex;justify-content:space-between;gap:12px;margin-bottom:7px}.queue{overflow:hidden}.qh,.qi{display:grid;grid-template-columns:110px minmax(260px,1fr) 190px 150px;gap:14px;align-items:center;padding:12px 16px}.qh{background:#202c3a;color:#8fa2b8;font-size:12px;text-transform:uppercase}.qi{border-top:1px solid #2c3a4d}.qf{min-width:0}.qf strong,.qf span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.qf span{color:#8fa2b8;font-size:12px;margin-top:3px}.badge{margin:0;font-size:13px}.badge.encoding{background:#155b8d}.badge.validating{background:#6d4b15}.badge.ready{background:#285637}.badge.waiting{background:#4b425e}.empty{padding:22px;color:#8fa2b8}@media(max-width:760px){body{padding:15px}.qh{display:none}.qi{grid-template-columns:1fr;gap:7px}.qi>div:last-child{text-align:left!important}}</style></head><body>
<h1>JellyPi Encoder</h1><div class="muted">Updates every 5 seconds. Queue compatibility refreshes every 2 minutes.</div><h2>Worker</h2><div id="worker" class="grid"></div><h2>Storage</h2><div id="storage" class="grid"></div><h2>Detailed queue <span id="count" class="muted"></span></h2><div id="queue" class="queue"></div><script>
const e=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const b=n=>{if(n==null)return'';let u=['B','KB','MB','GB','TB'],i=0;while(n>=1024&&i<4){n/=1024;i++}return n.toFixed(i>2?1:0)+' '+u[i]};const dt=s=>{let d=new Date(s);return !s||isNaN(d)?(s||''):d.toLocaleString()};async function refresh(){let d=await(await fetch('/api/overview',{cache:'no-store'})).json(),ws=d.workers||[d.worker||{}];worker.innerHTML=ws.map(s=>{let v=Number(s.progress||0);return `<div class="card"><div class="name">${e(s.worker||'encoder')}</div><div class="state ${s.state==='error'?'error':s.state==='completed'?'ok':''}">${e(s.state||'offline')}</div><progress max="100" value="${v}"></progress><div>${v}%</div><div class="file">${e(s.file||s.lastCompleted||s.decision||'Waiting for media')}</div><div class="meta">Files left to inspect: ${e(s.queue??0)}<br>Updated: ${e(dt(s.updated))}</div>${s.error?`<div class="error">${e(s.error)}</div>`:''}</div>`}).join('');storage.innerHTML=d.storage.map(x=>x.error?`<div class="card"><div class="name">${e(x.name)}</div><div class="error">${e(x.error)}</div></div>`:`<div class="card"><div class="row"><div class="name">${e(x.name)}</div><strong>${x.percent}%</strong></div><progress max="100" value="${x.percent}"></progress><div class="row meta"><span>${b(x.used)} used</span><span>${b(x.free)} free of ${b(x.total)}</span></div></div>`).join('');let q=d.queue||{},a=q.items||[];count.textContent='('+a.length+')';let h='<div class="qh"><div>Status</div><div>File</div><div>Action / worker</div><div style="text-align:right">Size / timing</div></div>';queue.innerHTML=h+(a.length?a.map(x=>`<div class="qi"><div><span class="badge ${e(x.state)}">${e(x.state)}</span></div><div class="qf"><strong title="${e(x.file)}">${e(x.name)}</strong><span>${e(x.file)}</span></div><div>${e(x.worker||x.decision||'')}</div><div style="text-align:right">${x.state==='encoding'||x.state==='validating'?e(x.progress??0)+'%':x.state==='waiting'?'Check '+e(dt(x.readyAt)):b(x.size)}</div></div>`).join(''):`<div class="empty">${q.scanning?'Scanning the library...':'No files currently need conversion.'}</div>`)}refresh();setInterval(refresh,5000)</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path.startswith("/api/overview"):
            data = json.dumps(overview()).encode()
            content_type = "application/json"
        else:
            data = HTML.encode()
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args) -> None:
        pass


def main() -> None:
    threading.Thread(target=scan_loop, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


if __name__ == "__main__":
    main()
