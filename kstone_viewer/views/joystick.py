"""Embedded chassis joystick: a tiny same-host HTTP endpoint serving an iframe page.

Viser has no custom pointer widget, so the page is served on a random port and an unguessable path and posts
normalized sticks as JSON. Like the viser server itself, the endpoint has no authentication; it binds to the same
host as the viewer. One browser owner drives at a time; a stale owner (no post for ``OWNER_TIMEOUT_S``) is dropped.
"""

from __future__ import annotations

import html
import json
import math
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

OWNER_TIMEOUT_S = 0.25


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("expected a finite number")
    return float(value)


class JoystickInput:
    """Thread-safe stick state shared by the HTTP handler and the chassis view."""

    def __init__(self, *, differential: bool, deadband: float) -> None:
        if not 0.0 <= deadband < 1.0:
            raise ValueError("joystick deadband must be in [0, 1)")
        self.differential, self.deadband = differential, deadband
        self._lock = threading.Lock()
        self._owner: str | None = None
        self._stamp = -math.inf
        self._sequences: dict[str, int] = {}
        self._stick = (0.0, 0.0, 0.0)
        self.enabled = False

    def accept(self, sample: dict[str, Any], now: float | None = None) -> None:
        owner, sequence, active = sample.get("owner"), sample.get("sequence"), sample.get("active")
        if not isinstance(owner, str) or not 1 <= len(owner) <= 80 or type(sequence) is not int or sequence < 0 \
                or not isinstance(active, bool):
            raise ValueError("invalid joystick event")
        vx, vy, wz = (_finite(sample.get(key, 0.0)) for key in ("vx", "vy", "wz"))
        vx, vy, wz = (0.0 if abs(v) < self.deadband else v for v in (vx, vy, wz))
        if self.differential:
            vy = 0.0
        length = max(1.0, math.hypot(vx, vy))
        stick = (vx / length, vy / length, max(-1.0, min(1.0, wz)))
        now = time.monotonic() if now is None else now
        with self._lock:
            if not self.enabled or sequence <= self._sequences.get(owner, -1):
                return
            if len(self._sequences) >= 128 and owner not in self._sequences:
                self._sequences.clear()
            self._sequences[owner] = sequence
            if now - self._stamp > OWNER_TIMEOUT_S:
                self._owner = None
            if self._owner not in (None, owner):
                return
            if active:
                self._stick, self._owner = stick, owner
            else:
                self._stick, self._owner = (0.0, 0.0, 0.0), None
            self._stamp = now

    def sample(self, now: float | None = None) -> tuple[float, float, float]:
        """Normalized stick; zero once the owner went quiet (tab closed, network loss)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._owner is None or now - self._stamp > OWNER_TIMEOUT_S:
                return 0.0, 0.0, 0.0
            return self._stick

    def release(self) -> None:
        with self._lock:
            self._stick, self._owner = (0.0, 0.0, 0.0), None


class JoystickServer:
    def __init__(self, host: str, stick: JoystickInput, read_state: Callable[[], dict[str, Any]]) -> None:
        self.path = "/" + secrets.token_urlsafe(24)
        route, page = self.path, joystick_page(stick.differential, stick.deadband).encode("utf-8")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _reply(self, content: bytes, kind: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(content)

            def do_GET(self) -> None:  # noqa: N802
                if self.path != route:
                    self.send_error(404)
                    return
                self._reply(page, "text/html; charset=utf-8")

            def do_POST(self) -> None:  # noqa: N802
                if self.path != route:
                    self.send_error(404)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                    if not 0 < size <= 2048:
                        raise ValueError("invalid request size")
                    sample = json.loads(self.rfile.read(size))
                    if not isinstance(sample, dict):
                        raise ValueError("expected an object")
                    stick.accept(sample)
                except (ValueError, TypeError):
                    self.send_error(400)
                    return
                self._reply(json.dumps(read_state()).encode("utf-8"), "application/json")

        bind = "" if host in ("0.0.0.0", "::") else host
        self.server = ThreadingHTTPServer((bind, 0), Handler)
        self.server.daemon_threads = True
        self.port = int(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
                                       name="kstone-joystick", daemon=True)
        self.thread.start()

    def iframe(self, height: int) -> str:
        # The iframe navigates to the joystick port on whatever host the browser used for the viewer.
        source = ("<script>const u=new URL(document.referrer||parent.location.href);"
                  f"u.port={json.dumps(str(self.port))};u.pathname={json.dumps(self.path)};"
                  "u.search='';u.hash='';location.replace(u.href);</script>")
        return (f"<iframe title=\"chassis joystick\" style=\"width:100%;height:{height}px;border:0;border-radius:12px;"
                f"display:block\" srcdoc=\"{html.escape(source, quote=True)}\"></iframe>")

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1.0)


_PAGE = r"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--fg:#1f2937;--muted:#6b7280;--bg:#f6f7fb;--pad:#ffffff;--ring:#d4d8e1;--cross:#e5e7eb;--xy:#3b82f6;--yaw:#f59e0b;--bad:#e5484d}
.dark{--fg:#e5e7eb;--muted:#9ca3af;--bg:#1f2126;--pad:#2a2d34;--ring:#3d414b;--cross:#353841}
*{box-sizing:border-box}
body{margin:0;font:12px ui-sans-serif,system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;
color:var(--fg);background:var(--bg);user-select:none;-webkit-user-select:none;padding:8px 6px 6px}
.pads{display:flex;justify-content:space-evenly;gap:6px}
.col{display:flex;flex-direction:column;align-items:center;gap:4px}
.title{font-weight:650;font-size:12px}
.pad{width:124px;height:124px;border-radius:50%;position:relative;background:
radial-gradient(circle,var(--pad) 0 58%,transparent 59%),
linear-gradient(transparent calc(50% - 1px),var(--cross) calc(50% - 1px) calc(50% + 1px),transparent calc(50% + 1px)),
linear-gradient(90deg,transparent calc(50% - 1px),var(--cross) calc(50% - 1px) calc(50% + 1px),transparent calc(50% + 1px)),var(--pad);
border:2px solid var(--ring);box-shadow:inset 0 2px 8px rgba(0,0,0,.08);touch-action:none;cursor:grab;outline:none}
.pad:focus-visible{box-shadow:0 0 0 3px rgba(59,130,246,.35)}
.pad.on{cursor:grabbing;border-color:var(--xy)}#yaw.on{border-color:var(--yaw)}
.knob{position:absolute;left:44px;top:44px;width:32px;height:32px;border-radius:50%;background:var(--xy);
box-shadow:0 2px 6px rgba(0,0,0,.25);pointer-events:none;transition:transform 60ms linear}
#yaw .knob{background:var(--yaw)}
.hint{color:var(--muted);font-size:11px;text-align:center;min-height:14px}
.row{display:flex;justify-content:center;gap:6px;margin-top:6px;flex-wrap:wrap}
.chip{font:600 11px ui-monospace,Menlo,Consolas,monospace;padding:2px 8px;border-radius:999px;background:rgba(127,127,127,.14)}
#state{margin-top:5px;text-align:center;font-weight:600;min-height:16px}
#state.ext{color:var(--bad)}.locked .pad{opacity:.45;cursor:not-allowed}
</style>
<body><div id="root">
<div class="pads"><div class="col"><div class="title" id="t-xy"></div>
<div id="xy" class="pad" role="application" tabindex="0"><div class="knob"></div></div><div class="hint" id="h-xy"></div></div>
<div class="col" id="yaw-col"__YAW_HIDDEN__><div class="title" id="t-yaw"></div>
<div id="yaw" class="pad" role="application" tabindex="0"><div class="knob"></div></div><div class="hint" id="h-yaw"></div></div></div>
<div class="row"><span class="chip" id="c-vx">vx 0.00</span><span class="chip" id="c-vy"__VY_HIDDEN__>vy 0.00</span><span class="chip" id="c-wz">wz 0.00</span></div>
<div id="state"></div><div class="hint" id="keys"></div></div>
<script>
const owner=(globalThis.crypto?.randomUUID?.()||String(Math.random()).slice(2));
const differential=__DIFFERENTIAL__, deadband=__DEADBAND__, R=46;
const dz=v=>Math.abs(v)<deadband?0:v;
let sequence=0, axes={vx:0,vy:0,wz:0}, pointers=new Map(), keys=new Set(), busy=false, remote=false, lang='en';
const t=(zh,en)=>lang==='zh'?zh:en, $=id=>document.getElementById(id);
function label(){
  $('t-xy').textContent=differential?t('行驶','Drive'):t('平移','Translate');
  $('t-yaw').textContent=t('旋转','Rotate');
  $('h-xy').textContent=differential?t('上 前进 · 左 逆时针','up forward · left CCW'):t('上 前进 · 左 左移','up forward · left strafe');
  $('h-yaw').textContent=t('左 逆时针 · 右 顺时针','left CCW · right CW');
  $('keys').textContent=t('点击后可用键盘 W/S A/D Q/E，Esc 停止','Click, then W/S A/D Q/E; Esc stops');
  $('xy').setAttribute('aria-label',$('t-xy').textContent);$('yaw').setAttribute('aria-label',$('t-yaw').textContent);
}
function keyAxes(){
  const k=c=>keys.has(c)?1:0;
  const fwd=k('w')-k('s'), side=k('a')-k('d'), turn=k('q')-k('e');
  return differential?{vx:fwd,vy:0,wz:side||turn}:{vx:fwd,vy:side,wz:turn};
}
function current(){
  if(keys.size&&!pointers.size)return keyAxes();
  return axes;
}
async function send(){
  if(busy)return;busy=true;
  try{
    const a=current();
    const r=await fetch(location.pathname,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({owner,sequence:++sequence,active:pointers.size>0||keys.size>0,...a}),keepalive:true});
    if(!r.ok)throw new Error('rejected');
    const s=await r.json();
    if(s.language!==lang){lang=s.language;label();}
    document.body.classList.toggle('dark',!!s.dark);
    $('root').classList.toggle('locked',!!s.external||!s.enabled);
    const st=$('state');
    if(s.external){
      if(!remote){remote=true;stopAll(false);}
      st.className='ext';st.textContent=t('外部控制中：','External control: ')+s.source;
    }else{
      remote=false;st.className='';
      st.textContent=!s.enabled?t('未就绪','Not ready'):(pointers.size||keys.size?t('控制中','Driving'):t('已回零','Neutral'));
    }
    $('xy').querySelector('.knob').style.transform=`translate(${-(differential?s.wz:s.vy)*R}px,${-s.vx*R}px)`;
    $('yaw').querySelector('.knob').style.transform=`translate(${-s.wz*R}px,0)`;
    $('c-vx').textContent=`vx ${s.out[0].toFixed(2)} m/s`;$('c-vy').textContent=`vy ${s.out[1].toFixed(2)} m/s`;
    $('c-wz').textContent=`wz ${s.out[2].toFixed(2)} rad/s`;
  }catch(e){$('state').className='ext';$('state').textContent=t('连接中断，底盘将超时停止','Disconnected; chassis times out');}
  finally{busy=false;}
}
function release(pad,notify=true){
  pointers.delete(pad.id);pad.classList.remove('on');
  if(pad.id==='xy'){axes.vx=0;axes.vy=0;if(differential)axes.wz=0;}else axes.wz=0;
  if(notify)send();
}
function stopAll(notify=true){keys.clear();for(const p of document.querySelectorAll('.pad'))release(p,false);if(notify)send();}
for(const id of (differential?['xy']:['xy','yaw'])){
  const pad=$(id);
  const move=e=>{
    if(pointers.get(id)!==e.pointerId)return;
    const b=pad.getBoundingClientRect();
    let x=(e.clientX-b.left-b.width/2)/R, y=id==='yaw'?0:(e.clientY-b.top-b.height/2)/R;
    const n=Math.max(1,Math.hypot(x,y));x/=n;y/=n;
    if(id==='xy'){axes.vx=dz(-y);if(differential){axes.vy=0;axes.wz=dz(-x);}else axes.vy=dz(-x);}else axes.wz=dz(-x);
    send();
  };
  pad.addEventListener('pointerdown',e=>{if(remote||pointers.has(id))return;pad.focus();pointers.set(id,e.pointerId);
    pad.classList.add('on');pad.setPointerCapture(e.pointerId);move(e);});
  pad.addEventListener('pointermove',move);
  for(const ev of ['pointerup','pointercancel','lostpointercapture'])
    pad.addEventListener(ev,e=>{if(pointers.get(id)===e.pointerId)release(pad);});
}
document.addEventListener('keydown',e=>{
  const k=e.key.toLowerCase();
  if(k==='escape'||k===' '){stopAll();e.preventDefault();return;}
  if('wasdqe'.includes(k)&&k.length===1&&!remote){keys.add(k);send();e.preventDefault();}
});
document.addEventListener('keyup',e=>{const k=e.key.toLowerCase();if(keys.delete(k))send();});
window.addEventListener('blur',()=>stopAll());window.addEventListener('pagehide',()=>stopAll());
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopAll();});
label();setInterval(send,80);send();
</script></body></html>"""


def joystick_page(differential: bool, deadband: float) -> str:
    return (_PAGE.replace("__DIFFERENTIAL__", "true" if differential else "false")
            .replace("__YAW_HIDDEN__", " hidden" if differential else "")
            .replace("__VY_HIDDEN__", " hidden" if differential else "")
            .replace("__DEADBAND__", repr(float(deadband))))
