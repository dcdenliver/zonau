"""
Backend ZONA.U: melayani website dan webhook WhatsApp Cloud API.

Alur:  Website / WhatsApp  ->  backend ini  ->  Langflow (flow zonausaha_agent)
Peta:  /peta?lat=..&lon=..&usaha=..  digambar dengan komponen zone_map.py yang sama
       dengan yang dipakai di Langflow (satu sumber logika).

Semua rahasia dibaca dari environment variable (lihat .env.example). Jangan commit .env.
"""
import json
import os
import re
import sys
import tempfile
import time

import httpx
from fastapi import BackgroundTasks, FastAPI, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "components"))
from lf_stub import install  # noqa: E402

install()
import zone_map  # noqa: E402

env = os.environ.get
LANGFLOW_RUN_URL = env("LANGFLOW_RUN_URL", "")        # URL endpoint /run flow zonausaha_agent
LANGFLOW_API_KEY = env("LANGFLOW_API_KEY", "")
LANGFLOW_BEARER = env("LANGFLOW_BEARER_TOKEN", "")    # untuk Langflow cloud yang memakai Bearer token
GEOJSON_URL = env("GEOJSON_URL", os.path.join(ROOT, "data", "zona_rdtr_pusat.geojson"))
ITBX_URL = env("ITBX_URL", os.path.join(ROOT, "data", "itbx.csv"))
PUBLIC_BASE_URL = env("PUBLIC_BASE_URL", "").rstrip("/")
WA_TOKEN = env("WA_TOKEN", "")
WA_PHONE_ID = env("WA_PHONE_ID", "")
WA_VERIFY_TOKEN = env("WA_VERIFY_TOKEN", "zonau-verify")
GRAPH_VERSION = env("GRAPH_VERSION", "v21.0")
TIMEOUT = float(env("LANGFLOW_TIMEOUT", "120"))

app = FastAPI(title="ZONA.U")
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
_seen_wa_ids = {}


# --------------------------------------------------------------------------- Langflow
def _extract_text(data):
    """Ambil teks jawaban dari respons /run Langflow (struktur bisa sedikit berbeda antar versi)."""
    try:
        return data["outputs"][0]["outputs"][0]["results"]["message"]["text"]
    except (KeyError, IndexError, TypeError):
        pass
    found = []

    def walk(o):
        if isinstance(o, dict):
            if isinstance(o.get("text"), str) and o["text"].strip():
                found.append(o["text"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return found[-1] if found else "Maaf, agent tidak memberikan jawaban."


async def ask_agent(message: str, session_id: str) -> str:
    if not LANGFLOW_RUN_URL:
        return "Konfigurasi LANGFLOW_RUN_URL belum diisi di server."
    headers = {"Content-Type": "application/json", "ngrok-skip-browser-warning": "1"}
    if LANGFLOW_API_KEY:
        headers["x-api-key"] = LANGFLOW_API_KEY
    if LANGFLOW_BEARER:
        headers["Authorization"] = f"Bearer {LANGFLOW_BEARER}"
    payload = {"input_value": message, "input_type": "chat", "output_type": "chat",
               "session_id": session_id}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(LANGFLOW_RUN_URL, headers=headers, json=payload)
    if r.status_code >= 400:
        return f"Maaf, layanan sedang bermasalah (Langflow {r.status_code}). Coba lagi sebentar."
    return _extract_text(r.json())


# --------------------------------------------------------------------------- Website
@app.get("/", response_class=HTMLResponse)
async def home():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.post("/api/cek")
async def api_cek(request: Request):
    body = await request.json()
    msg = str(body.get("pesan", "")).strip()
    sid = str(body.get("session_id") or f"web-{int(time.time() * 1000)}")
    if not msg:
        return JSONResponse({"error": "Pesan kosong."}, status_code=400)
    if len(msg) > 1000:
        return JSONResponse({"error": "Pesan terlalu panjang (maks. 1000 karakter)."}, status_code=400)
    t0 = time.time()
    jawaban = await ask_agent(msg, sid)
    return {"jawaban": jawaban, "detik": round(time.time() - t0, 1), "session_id": sid}


@app.get("/peta", response_class=HTMLResponse)
async def peta(lat: float = Query(...), lon: float = Query(...), usaha: str = Query("")):
    out_dir = os.path.join(tempfile.gettempdir(), "zonau_maps")
    comp = zone_map.ZoneMapComponent(
        query=f"{lat}, {lon} | {usaha}", geojson_path=GEOJSON_URL, csv_path=ITBX_URL,
        output_dir=out_dir, enable_geocoding=False)
    text = comp.build_result().text
    try:
        info = json.loads(text.split("DATA_JSON: ", 1)[1])
        with open(info["map_path"], encoding="utf-8") as f:
            return HTMLResponse(f.read())
    except Exception:
        return HTMLResponse(f"<pre>{text}</pre>", status_code=500)


@app.get("/health")
async def health():
    return {"ok": True, "langflow": bool(LANGFLOW_RUN_URL), "whatsapp": bool(WA_TOKEN and WA_PHONE_ID)}


PRIVACY_HTML = """<!doctype html><html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Kebijakan Privasi ZONA.U</title>
<style>body{font-family:Arial,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;line-height:1.6;color:#16232A}</style></head><body>
<h1>Kebijakan Privasi ZONA.U</h1>
<p>ZONA.U adalah prototipe hackathon (Hackathon Nasional Hacktiv8 × IBM SkillsBuild 2026) yang membantu pelaku UMKM mengecek kesesuaian lokasi usaha dengan RDTR DKI Jakarta.</p>
<h2>Data yang diproses</h2>
<ul><li>Teks pertanyaan yang Anda kirim, termasuk lokasi (alamat, koordinat, atau lokasi yang dibagikan) dan jenis usaha.</li>
<li>Untuk WhatsApp: nomor WhatsApp pengirim, hanya untuk mengirim balasan.</li></ul>
<h2>Penggunaan data</h2>
<p>Data hanya dipakai untuk menjawab pertanyaan Anda. Kami tidak meminta nama, NIK, atau data pribadi lain, dan tidak menjual atau membagikan data kepada pihak ketiga selain layanan yang diperlukan untuk memproses jawaban (Langflow, penyedia model AI, OpenStreetMap Nominatim, dan WhatsApp Cloud API).</p>
<h2>Penyimpanan dan penghapusan</h2>
<p>Server tidak menyimpan riwayat percakapan secara permanen. Untuk meminta penghapusan data, kirim pesan "HAPUS DATA" ke nomor bot atau hubungi tim melalui repositori github.com/dcdenliver/zonau.</p>
<h2>Batasan</h2>
<p>Hasil ZONA.U adalah gambaran awal, bukan keputusan KKPR resmi. Keputusan resmi ada di OSS dan DPMPTSP.</p>
</body></html>"""


@app.get("/privasi", response_class=HTMLResponse)
async def privasi():
    return HTMLResponse(PRIVACY_HTML)


# --------------------------------------------------------------------------- WhatsApp
def to_whatsapp(text: str) -> str:
    """Markdown sederhana -> format WhatsApp."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1: \2", text)
    return text[:4000]


async def wa_send(to: str, text: str):
    if not (WA_TOKEN and WA_PHONE_ID):
        print("[WA] WA_TOKEN/WA_PHONE_ID belum diisi", flush=True)
        return
    url = f"https://graph.facebook.com/{GRAPH_VERSION}/{WA_PHONE_ID}/messages"
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text",
               "text": {"preview_url": True, "body": to_whatsapp(text)}}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, headers={"Authorization": f"Bearer {WA_TOKEN}"}, json=payload)
    if r.status_code >= 400:
        print(f"[WA] GAGAL kirim ke {to[-4:]}: HTTP {r.status_code} {r.text[:300]}", flush=True)
    else:
        print(f"[WA] terkirim ke ...{to[-4:]}", flush=True)


async def handle_wa_message(sender: str, text: str):
    await wa_send(sender, "⏳ Sedang mengecek zona dan izin usaha, mohon tunggu sekitar 30–60 detik...")
    jawaban = await ask_agent(text, f"wa-{sender}")
    await wa_send(sender, jawaban)


@app.get("/webhook")
async def wa_verify(request: Request):
    q = request.query_params
    if q.get("hub.mode") == "subscribe" and q.get("hub.verify_token") == WA_VERIFY_TOKEN:
        return PlainTextResponse(q.get("hub.challenge", ""))
    return PlainTextResponse("forbidden", status_code=403)


@app.post("/webhook")
async def wa_receive(request: Request, bg: BackgroundTasks):
    data = await request.json()
    print("[WA] webhook masuk:", json.dumps(data)[:200], flush=True)
    now = time.time()
    for k in [k for k, t in _seen_wa_ids.items() if now - t > 3600]:
        _seen_wa_ids.pop(k, None)
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            for st in change.get("value", {}).get("statuses", []) or []:
                print(f"[WA] status pesan: {st.get('status')} ke ...{str(st.get('recipient_id', ''))[-4:]}"
                      f" errors={json.dumps(st.get('errors', []), ensure_ascii=False)}", flush=True)
            for m in change.get("value", {}).get("messages", []) or []:
                mid, sender = m.get("id"), m.get("from")
                if not sender or mid in _seen_wa_ids:      # Meta bisa mengirim ulang pesan yang sama
                    continue
                _seen_wa_ids[mid] = now
                kind = m.get("type")
                if kind == "text":
                    text = m["text"]["body"]
                elif kind == "location":
                    loc = m["location"]
                    text = f"Lokasi saya: {loc['latitude']}, {loc['longitude']}"
                    if loc.get("name") or loc.get("address"):
                        text += f" ({loc.get('name', '')} {loc.get('address', '')})".rstrip()
                else:
                    bg.add_task(wa_send, sender, "Kirim pesan teks berisi lokasi + jenis usaha, "
                                "atau bagikan lokasi (share location) lalu sebutkan jenis usahanya.")
                    continue
                bg.add_task(handle_wa_message, sender, text)
    return {"ok": True}   # balas cepat; jawaban dikirim di background agar Meta tidak mengirim ulang
