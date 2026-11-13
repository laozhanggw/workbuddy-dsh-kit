# -*- coding: utf-8 -*-
"""批量加号台 —— 生成「永久有效」的扫码链接。

给对方的是一条链接（/s/<slot>）：点开是一个二维码页。
背后那张二维码到期前会自动换新，页面自己跟着刷新，所以**链接本身一直有效**，
直到有人扫完、或者你手动取消。

用法：
    python batch_add.py                       # 只给本机用 http://127.0.0.1:8790
    python batch_add.py --host 0.0.0.0        # 同一 WiFi 的人也能打开（显示局域网地址）
    python batch_add.py --host 0.0.0.0 --tunnel   # 用 cloudflared 开一个公网链接

面板密码只在内存里换 token，不落盘、不写日志。
"""

import argparse
import io
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GATEWAY = "http://127.0.0.1:8788"
PORT = 8790
HOST = "127.0.0.1"
PUBLIC_BASE = ""          # 对外展示的地址前缀（隧道/域名），空则自动
ROTATE_BEFORE = 300       # 到期前这么多秒自动换新二维码
ROTATE_MIN = 60           # 但至少活这么久才换，免得一直换
CF_BIN = r"E:\AI工作区\20261109-沉浸式翻译接WorkBuddy\tools\cloudflared.exe"

_lock = threading.Lock()
_token = ""               # 面板会话 token（内存）
_items = []               # [{slot, state, url, realm, created, status, message, account, gen}]


# --------------------------------------------------------------------------- 网关
def gw(method, path, payload=None, token="", timeout=30):
    url = GATEWAY + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-Panel-Token"] = token
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body)
            except Exception:
                return resp.status, {"raw": body[:400]}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except Exception:
            return exc.code, {"raw": body[:400]}
    except Exception as exc:
        return 0, {"error": str(exc)}


def err_text(payload):
    if not isinstance(payload, dict):
        return str(payload)
    e = payload.get("error")
    if isinstance(e, dict):
        return e.get("message") or json.dumps(e, ensure_ascii=False)
    if isinstance(e, str):
        return e
    return payload.get("raw") or json.dumps(payload, ensure_ascii=False)[:200]


def login_ttl():
    try:
        import pathlib
        here = pathlib.Path(__file__).resolve().parent.parent
        src = (here / "wb_accounts.py").read_text(encoding="utf-8")
        m = re.search(r"LOGIN_TTL_SECONDS\s*=\s*(\d+)", src)
        if m:
            return int(m.group(1))
    except Exception:
        pass
    return 600


def qr_png(data, scale=7):
    import segno
    qr = segno.make(data, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=scale, border=2)
    return buf.getvalue()


def new_slot():
    return secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:12]


# --------------------------------------------------------------------------- 页面
SLOT_PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>扫码登录 WorkBuddy</title>
<style>
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:#0f1115;color:#e6e9ef;font:15px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif;display:flex;align-items:center;justify-content:center;padding:20px}
.box{width:100%;max-width:420px;text-align:center}
h1{font-size:17px;font-weight:600;margin:0 0 6px}
.sub{color:#9aa4b2;font-size:13px;margin-bottom:18px}
.qrwrap{background:#fff;border-radius:14px;padding:14px;display:inline-block;box-shadow:0 8px 30px rgba(0,0,0,.45)}
.qrwrap img{display:block;width:min(72vw,280px);height:auto}
.tip{color:#9aa4b2;font-size:12.5px;margin-top:14px;line-height:1.8}
.badge{display:inline-block;margin-top:14px;padding:5px 14px;border-radius:999px;font-size:13px}
.b-wait{background:#1f2a37;color:#d29922}
.b-ok{background:#12301c;color:#3fb950}
.b-bad{background:#33161a;color:#f85149}
.big{font-size:20px;font-weight:600;margin:10px 0}
.hide{display:none}
</style></head>
<body><div class="box">
  <h1 id="title">扫码登录</h1>
  <div class="sub" id="sub">用手机扫下面的二维码，按提示登录 WorkBuddy 国内版</div>

  <div id="qrBox" class="qrwrap"><img id="qr" alt="扫码登录"></div>

  <div id="stateBox">
    <div class="badge b-wait" id="badge">等待扫码…</div>
    <div class="tip" id="countdown"></div>
  </div>

  <div id="doneBox" class="hide">
    <div class="big" id="doneText"></div>
    <div class="tip">可以关掉这个页面了。</div>
  </div>

  <div class="tip">二维码会自动换新，这个链接一直有效，不用刷新。</div>
</div>
<script>
const SLOT = location.pathname.split('/').pop();
let lastGen = -1, timer = null;

function fmt(s){
  if(s <= 0) return '已过期';
  const m = Math.floor(s/60), x = s % 60;
  return '剩 ' + m + ' 分 ' + (x<10?'0':'') + x + ' 秒自动换新';
}

async function tick(){
  let j;
  try{
    const r = await fetch('/api/slot/' + encodeURIComponent(SLOT), {cache:'no-store'});
    if(!r.ok){ throw new Error('gone'); }
    j = await r.json();
  }catch(e){
    document.getElementById('qrBox').classList.add('hide');
    document.getElementById('stateBox').classList.add('hide');
    document.getElementById('doneBox').classList.remove('hide');
    document.getElementById('title').textContent = '链接已失效';
    document.getElementById('doneText').textContent = '让对方重新发一个';
    if(timer){ clearInterval(timer); timer = null; }
    return;
  }

  if(j.status === 'ok'){
    document.getElementById('qrBox').classList.add('hide');
    document.getElementById('stateBox').classList.add('hide');
    document.getElementById('doneBox').classList.remove('hide');
    document.getElementById('title').textContent = '登录成功';
    document.getElementById('doneText').textContent = '已加入：' + (j.account || '新账号');
    if(timer){ clearInterval(timer); timer = null; }
    return;
  }
  if(j.status === 'error'){
    document.getElementById('qrBox').classList.add('hide');
    document.getElementById('stateBox').classList.add('hide');
    document.getElementById('doneBox').classList.remove('hide');
    document.getElementById('title').textContent = '链接已取消';
    document.getElementById('doneText').textContent = '让对方重新发一个';
    if(timer){ clearInterval(timer); timer = null; }
    return;
  }

  if(j.gen !== lastGen){
    lastGen = j.gen;
    document.getElementById('qr').src = '/api/slot-qr/' + encodeURIComponent(SLOT) + '?g=' + j.gen + '&t=' + Date.now();
  }
  document.getElementById('countdown').textContent = fmt(j.expiresIn || 0);
}

tick();
timer = setInterval(tick, 2500);
</script></body></html>
"""

ADMIN_PAGE = r"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>批量加号台 · WorkBuddy</title>
<style>
:root{--bg:#0f1115;--card:#181c23;--line:#2a3140;--fg:#e6e9ef;--dim:#9aa4b2;--ok:#3fb950;--bad:#f85149;--warn:#d29922;--acc:#2f81f7}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:24px}
h1{font-size:20px;margin:0 0 4px}
.sub{color:var(--dim);font-size:13px;margin-bottom:18px}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin-bottom:16px}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
input,select{background:#0d1016;border:1px solid var(--line);color:var(--fg);border-radius:8px;padding:8px 10px;font-size:13px}
input[type=password]{width:220px}
input[type=number]{width:90px}
button{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:8px 14px;font-size:13px;cursor:pointer}
button.sec{background:#242b36}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:10px;border-bottom:1px solid var(--line);vertical-align:middle}
th{color:var(--dim);font-weight:600}
.tag{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px}
.t-pending{background:#1f2a37;color:var(--warn)}
.t-ok{background:#12301c;color:var(--ok)}
.t-bad{background:#33161a;color:var(--bad)}
.hint{color:var(--dim);font-size:12px;margin-top:6px}
.share{font-size:12px;word-break:break-all;color:#7fb2ff}
.qr{background:#fff;padding:5px;border-radius:8px;display:block;cursor:zoom-in}
#lb{position:fixed;inset:0;background:rgba(0,0,0,.85);display:none;align-items:center;justify-content:center;z-index:99;flex-direction:column;gap:12px}
#lb img{background:#fff;padding:14px;border-radius:12px;width:min(78vw,520px)}
</style></head>
<body><div class="wrap">
<h1>批量加号台</h1>
<div class="sub">给每人发一条<b>分享链接</b>，他点开就是二维码页；二维码到期会自动换新，<b>链接本身一直有效</b>。</div>

<div class="card" id="loginCard">
  <div class="row">
    <span>面板密码</span>
    <input type="password" id="pw" placeholder="网关面板密码">
    <button onclick="doLogin()">登录网关</button>
    <span id="loginMsg" class="hint"></span>
  </div>
</div>

<div class="card" id="genCard" style="display:none">
  <div class="row">
    <span>版本</span>
    <select id="realm"><option value="cn">国内版</option><option value="intl">国际版</option></select>
    <span>生成</span>
    <input type="number" id="n" value="3" min="1" max="20">
    <span>个号位</span>
    <button onclick="doStart()">生成分享链接</button>
    <button class="sec" onclick="doClear()">清空</button>
    <span class="hint" id="base"></span>
  </div>
</div>

<div class="card" id="listCard" style="display:none">
  <table><thead><tr>
    <th style="width:44px">#</th><th style="width:112px">二维码</th><th>分享链接（发给对方这个）</th>
    <th style="width:130px">状态</th><th style="width:120px">操作</th>
  </tr></thead><tbody id="rows"></tbody></table>
</div>

<div class="hint">对方扫完，状态变「已加入」，账号自动进国内池。链接没被用掉就一直有效。</div>
</div>
<div id="lb" onclick="this.style.display='none'"><img id="lbImg" alt=""></div>

<script>
let timer = null;

async function api(path, body){
  const opt = body ? {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)} : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(j.error || ('HTTP '+r.status));
  return j;
}

async function doLogin(){
  const msg = document.getElementById('loginMsg');
  msg.textContent = '正在登录…';
  try{
    const j = await api('/api/login', {password: document.getElementById('pw').value});
    document.getElementById('pw').value = '';
    document.getElementById('loginCard').style.display = 'none';
    document.getElementById('genCard').style.display = '';
    document.getElementById('listCard').style.display = '';
    document.getElementById('base').textContent = '对外地址：' + (j.base || '');
    refresh(); if(!timer) timer = setInterval(refresh, 3000);
  }catch(e){ msg.textContent = '登录失败：' + e.message; }
}

async function doStart(){
  const n = parseInt(document.getElementById('n').value||'3',10);
  try{
    const j = await api('/api/start', {n:n, realm: document.getElementById('realm').value});
    if(j.failed && j.failed.length){ alert('部分失败：' + j.failed.join(' / ')); }
    refresh();
  }catch(e){ alert('生成失败：' + e.message); }
}

async function doClear(){ await api('/api/clear', {}); refresh(); }
async function doRefreshOne(slot){ await api('/api/refresh', {slot: slot}); refresh(); }
async function doCancel(slot){ await api('/api/cancel', {slot: slot}); refresh(); }

function copyText(t){
  navigator.clipboard.writeText(t).then(()=>{
    const d = document.createElement('div');
    d.textContent = '已复制';
    d.style.cssText = 'position:fixed;right:20px;bottom:20px;background:#1f2937;color:#fff;padding:8px 14px;border-radius:8px;z-index:99';
    document.body.appendChild(d); setTimeout(()=>d.remove(), 1200);
  }).catch(()=>alert('复制失败，手动选中吧：' + t));
}

async function refresh(){
  let j;
  try{ j = await api('/api/status'); }catch(e){ return; }
  const rows = document.getElementById('rows');
  rows.innerHTML = '';
  (j.items||[]).forEach(function(it, i){
    const cls = it.status==='ok'?'t-ok':(it.status==='pending'?'t-pending':'t-bad');
    const label = {ok:'已加入', pending:'等扫码', expired:'已失效', unknown:'未知', error:'已取消'}[it.status] || it.status;
    const qrUrl = '/api/slot-qr/' + encodeURIComponent(it.slot) + '?g=' + it.gen;
    const share = (j.base || '') + '/s/' + it.slot;
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td>'+(i+1)+'</td>'+
      '<td>'+(it.status==='pending'
        ? '<img class="qr" src="'+qrUrl+'" width="100" height="100" alt="二维码" onclick="document.getElementById(\'lbImg\').src=\''+qrUrl+'\';document.getElementById(\'lb\').style.display=\'flex\'">'
        : '<div class="hint">—</div>')+'</td>'+
      '<td><div class="share">'+share+'</div>'+
        (it.status==='pending' ? '<button class="sec" style="margin-top:6px;font-size:12px" onclick="copyText(\''+share+'\')">复制链接</button>' : '')+
        '<div class="hint">剩 ' + Math.round((it.expiresIn||0)/60) + ' 分钟后自动换新（链接不变）</div></td>'+
      '<td><span class="tag '+cls+'">'+label+'</span>'+(it.account?'<div class="hint"><b>'+it.account+'</b></div>':'')+'</td>'+
      '<td>'+(it.status==='pending'
        ? '<button class="sec" onclick="doRefreshOne(\''+it.slot+'\')">换一张</button> '+
          '<button class="sec" onclick="doCancel(\''+it.slot+'\')">取消</button>'
        : '')+'</td>';
    rows.appendChild(tr);
  });
}
</script></body></html>
"""


# --------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        ct = ctype + ("; charset=utf-8" if ("text" in ctype or "json" in ctype) else "")
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _find(self, key, value):
        with _lock:
            return next((x for x in _items if x.get(key) == value), None)

    def do_GET(self):
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            return self._send(200, ADMIN_PAGE, "text/html")

        if path.startswith("/s/"):
            return self._send(200, SLOT_PAGE, "text/html")

        if path == "/api/status":
            now = time.time()
            ttl = login_ttl()
            with _lock:
                items = []
                for x in _items:
                    it = dict(x)
                    it["expiresIn"] = max(0, int(x["created"] + ttl - now))
                    items.append(it)
            return self._json(200, {"items": items, "ttl": ttl, "base": public_base()})

        if path.startswith("/api/slot/"):
            slot = path[len("/api/slot/"):]
            item = self._find("slot", slot)
            if not item:
                return self._json(404, {"error": "no such slot"})
            ttl = login_ttl()
            return self._json(200, {
                "status": item["status"], "account": item.get("account") or "",
                "message": item.get("message") or "", "gen": int(item.get("gen") or 0),
                "expiresIn": max(0, int(item["created"] + ttl - time.time())),
            })

        if path.startswith("/api/slot-qr/"):
            slot = path[len("/api/slot-qr/"):]
            item = self._find("slot", slot)
            if not item:
                return self._json(404, {"error": "no such slot"})
            try:
                return self._send(200, qr_png(item["url"]), "image/png")
            except Exception as exc:
                return self._json(500, {"error": "qr failed: %s" % exc})

        return self._json(404, {"error": "not found"})

    def do_POST(self):
        global _token
        path = urllib.parse.urlsplit(self.path).path
        payload = self._body()

        if path == "/api/login":
            code, resp = gw("POST", "/panel/login", {"password": payload.get("password") or ""})
            if code != 200 or not resp.get("token"):
                return self._json(401, {"error": err_text(resp)})
            with _lock:
                _token = resp["token"]
            return self._json(200, {"ok": True, "ttl": login_ttl(), "base": public_base()})

        if path == "/api/start":
            with _lock:
                token = _token
            if not token:
                return self._json(401, {"error": "请先登录网关"})
            n = max(1, min(20, int(payload.get("n") or 3)))
            realm = payload.get("realm") or "cn"
            made, failed = [], []
            for _ in range(n):
                code, resp = gw("POST", "/accounts/login/start",
                                {"platform": "CLI", "realm": realm}, token=token)
                if code == 200 and resp.get("state") and resp.get("authUrl"):
                    slot = new_slot()
                    with _lock:
                        _items.append({"slot": slot, "state": resp["state"], "url": resp["authUrl"],
                                       "realm": realm, "created": time.time(), "status": "pending",
                                       "message": "", "account": "", "gen": 0})
                    made.append(slot)
                else:
                    failed.append(err_text(resp))
            return self._json(200, {"made": made, "failed": failed})

        if path == "/api/clear":
            with _lock:
                _items.clear()
            return self._json(200, {"ok": True})

        if path in ("/api/cancel", "/api/refresh"):
            slot = str(payload.get("slot") or "")
            item = self._find("slot", slot)
            if not item:
                return self._json(404, {"error": "no such slot"})
            with _lock:
                token = _token
            if not token:
                return self._json(401, {"error": "请先登录网关"})

            if path == "/api/cancel":
                with _lock:
                    item["status"] = "error"
                    item["message"] = "已取消"
                gw("POST", "/accounts/login/cancel", {"state": item["state"]}, token=token)
                return self._json(200, {"ok": True})

            code, resp = gw("POST", "/accounts/login/start",
                            {"platform": "CLI", "realm": item.get("realm") or "cn"}, token=token)
            if code != 200 or not resp.get("state"):
                return self._json(502, {"error": err_text(resp)})
            old_state = item["state"]
            with _lock:
                item["state"] = resp["state"]
                item["url"] = resp["authUrl"]
                item["created"] = time.time()
                item["gen"] = int(item.get("gen") or 0) + 1
                item["status"] = "pending"
                item["message"] = ""
            gw("POST", "/accounts/login/cancel", {"state": old_state}, token=token)
            return self._json(200, {"ok": True})

        return self._json(404, {"error": "not found"})


# --------------------------------------------------------------------------- 后台
def poll_loop():
    global _token
    while True:
        with _lock:
            token = _token
            ttl = login_ttl()
            pending = [dict(x) for x in _items if x["status"] == "pending"]
        for snap in pending:
            if not token:
                break
            state = snap["state"]
            code, resp = gw("GET", "/accounts/login/poll?state=" + urllib.parse.quote(state),
                            token=token, timeout=20)
            status = resp.get("status") if isinstance(resp, dict) else None
            with _lock:
                item = next((x for x in _items if x["state"] == state), None)
                if item is not None:
                    if code == 200 and status:
                        item["status"] = status
                        item["message"] = resp.get("message") or ""
                        acct = resp.get("account") or {}
                        if isinstance(acct, dict) and acct.get("nickname"):
                            item["account"] = acct["nickname"]
                    elif code == 401:
                        item["message"] = "面板会话失效，请重新登录"

        now = time.time()
        rotate = []
        with _lock:
            for x in _items:
                if x["status"] != "pending":
                    continue
                if now - x["created"] > max(ROTATE_MIN, ttl - ROTATE_BEFORE):
                    rotate.append(dict(x))
        for snap in rotate:
            if not token:
                break
            code, resp = gw("POST", "/accounts/login/start",
                            {"platform": "CLI", "realm": snap.get("realm") or "cn"}, token=token)
            if code == 200 and resp.get("state"):
                old = snap["state"]
                with _lock:
                    item = next((x for x in _items if x["state"] == old), None)
                    if item is not None:
                        item["state"] = resp["state"]
                        item["url"] = resp["authUrl"]
                        item["created"] = time.time()
                        item["gen"] = int(item.get("gen") or 0) + 1
                        item["message"] = "二维码已自动换新"
                gw("POST", "/accounts/login/cancel", {"state": old}, token=token)
        time.sleep(3)


# --------------------------------------------------------------------------- 工具
def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def public_base():
    if PUBLIC_BASE:
        return PUBLIC_BASE.rstrip("/")
    if HOST in ("0.0.0.0", "::"):
        return "http://%s:%d" % (lan_ip(), PORT)
    return "http://127.0.0.1:%d" % PORT


def start_tunnel(port):
    """cloudflared quick tunnel; returns the public base URL or ''."""
    global PUBLIC_BASE
    if not os.path.exists(CF_BIN):
        print("没找到 cloudflared：%s" % CF_BIN, flush=True)
        return ""
    try:
        proc = subprocess.Popen(
            [CF_BIN, "tunnel", "--url", "http://127.0.0.1:%d" % port, "--no-autoupdate"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        print("cloudflared 起不来：%s" % exc, flush=True)
        return ""
    deadline = time.time() + 40
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        m = re.search(r"https://[-\w]+\.trycloudflare\.com", line)
        if m:
            PUBLIC_BASE = m.group(0)
            print("公网地址：%s" % PUBLIC_BASE, flush=True)
            threading.Thread(target=lambda: [None for _ in proc.stdout], daemon=True).start()
            return PUBLIC_BASE
    print("隧道没拿到地址（可能要开代理或者被墙）", flush=True)
    return ""


def already_running(port):
    s = socket.socket()
    s.settimeout(1)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except Exception:
        return False
    finally:
        s.close()


def open_page(url):
    try:
        import webbrowser
        webbrowser.open(url)
    except Exception:
        pass


def main():
    global GATEWAY, PORT, HOST, PUBLIC_BASE, ROTATE_BEFORE, ROTATE_MIN
    ap = argparse.ArgumentParser(description="批量生成 WorkBuddy 扫码链接")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--host", default=HOST, help="127.0.0.1 只给本机；0.0.0.0 让局域网也能打开")
    ap.add_argument("--gateway", default=GATEWAY)
    ap.add_argument("--public-base", default="", help="对外地址前缀，比如 https://xxx.trycloudflare.com")
    ap.add_argument("--tunnel", action="store_true", help="用 cloudflared 开一个临时的公网地址")
    ap.add_argument("--rotate-before", type=int, default=ROTATE_BEFORE)
    ap.add_argument("--rotate-min", type=int, default=ROTATE_MIN)
    args = ap.parse_args()

    PORT = args.port
    HOST = args.host
    GATEWAY = args.gateway.rstrip("/")
    PUBLIC_BASE = args.public_base
    ROTATE_BEFORE = args.rotate_before
    ROTATE_MIN = args.rotate_min

    if already_running(PORT):
        print("批量加号台已经在跑了：http://127.0.0.1:%d" % PORT)
        open_page("http://127.0.0.1:%d" % PORT)
        return

    threading.Thread(target=poll_loop, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("批量加号台：http://%s:%d  (网关 %s)" % (HOST, PORT, GATEWAY), flush=True)

    if args.tunnel:
        start_tunnel(PORT)
    if PUBLIC_BASE:
        print("对外地址前缀：%s" % PUBLIC_BASE, flush=True)

    if HOST in ("127.0.0.1", "localhost"):
        open_page("http://127.0.0.1:%d" % PORT)
    srv.serve_forever()


if __name__ == "__main__":
    main()
