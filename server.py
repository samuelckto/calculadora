#!/usr/bin/env python3
"""Servidor del cotizador 3D: sirve la página y guarda los datos compartidos en data.json.

Uso:  python3 server.py [puerto]     (por defecto 8000)
"""
import datetime
import json
import os
import shutil
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

BASE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(BASE, "calculadora_impresion3d.html")
DATA = os.path.join(BASE, "data.json")
BACKUPS = os.path.join(BASE, "backups")
COLLECTIONS = ("history", "expenses")
SHARED = ("rate", "electricity", "labor", "design", "extras", "failure", "matMargin", "margin")
MAX_BODY = 8 * 1024 * 1024
LOCK = threading.Lock()


def load():
    try:
        with open(DATA, encoding="utf-8") as f:
            d = json.load(f)
    except (FileNotFoundError, ValueError):
        d = {}
    d.setdefault("rev", 0)
    for c in COLLECTIONS:
        d.setdefault(c, [])
    d.setdefault("settings", {})
    return d


STATE = load()


def save():
    os.makedirs(BACKUPS, exist_ok=True)
    bak = os.path.join(BACKUPS, "data-%s.json" % datetime.date.today().isoformat())
    if os.path.exists(DATA) and not os.path.exists(bak):
        shutil.copy2(DATA, bak)
    tmp = DATA + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(STATE, f, ensure_ascii=False)
    os.replace(tmp, DATA)


def valid_item(item):
    return isinstance(item, dict) and isinstance(item.get("id"), str) and 0 < len(item["id"]) <= 80


def apply_op(op):
    kind = op.get("op")
    col = op.get("collection")
    if kind == "put" and col in COLLECTIONS and valid_item(op.get("item")):
        item = op["item"]
        items = STATE[col]
        for i, it in enumerate(items):
            if it.get("id") == item["id"]:
                items[i] = item
                break
        else:
            items.append(item)
    elif kind == "delete" and col in COLLECTIONS and isinstance(op.get("id"), str):
        STATE[col] = [it for it in STATE[col] if it.get("id") != op["id"]]
    elif kind == "clear" and col in COLLECTIONS:
        STATE[col] = []
    elif kind == "settings" and isinstance(op.get("values"), dict):
        st = STATE["settings"]
        for k in SHARED:
            if k in op["values"]:
                st[k] = str(op["values"][k])[:20]
        st["stamp"] = "%d-%d" % (time.time() * 1000, STATE["rev"] + 1)
    elif kind == "import":
        for c in COLLECTIONS:
            have = {it.get("id") for it in STATE[c]}
            for item in op.get(c) or []:
                if valid_item(item) and item["id"] not in have:
                    STATE[c].append(item)
                    have.add(item["id"])
    else:
        return False
    STATE["rev"] += 1
    save()
    return True


class Handler(BaseHTTPRequestHandler):
    server_version = "Cotizador3D"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.log_date_time_string(), fmt % args))

    def reply(self, body, code=200, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == "/api/state":
            since = parse_qs(url.query).get("since", [None])[0]
            with LOCK:
                if since is not None and since == str(STATE["rev"]):
                    out = json.dumps({"same": True, "rev": STATE["rev"]})
                else:
                    out = json.dumps(STATE, ensure_ascii=False)
            self.reply(out.encode("utf-8"))
        elif url.path in ("/", "/index.html", "/calculadora_impresion3d.html"):
            try:
                with open(PAGE, "rb") as f:
                    self.reply(f.read(), ctype="text/html; charset=utf-8")
            except OSError:
                self.reply(b"No se encontro calculadora_impresion3d.html", 404, "text/plain; charset=utf-8")
        else:
            self.reply(b"No encontrado", 404, "text/plain; charset=utf-8")

    def do_POST(self):
        if urlsplit(self.path).path != "/api/op":
            return self.reply(b"No encontrado", 404, "text/plain; charset=utf-8")
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                raise ValueError
            op = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(op, dict):
                raise ValueError
        except ValueError:
            return self.reply({"error": "solicitud invalida"}, 400)
        with LOCK:
            if not apply_op(op):
                return self.reply({"error": "operacion invalida"}, 400)
            out = json.dumps(STATE, ensure_ascii=False)
        self.reply(out.encode("utf-8"))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print("Cotizador 3D en http://0.0.0.0:%d  (datos: %s)" % (port, DATA))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
