from langflow.custom import Component
from langflow.io import BoolInput, IntInput, MessageTextInput, MultilineInput, Output, StrInput
from langflow.schema.message import Message

# ---------------------------------------------------------------------------
# Helper bersama ZonaUsaha (disalin ke tiap komponen karena custom component
# Langflow harus berdiri sendiri dalam satu file).
# Python murni: tidak butuh shapely / geopandas.
# ---------------------------------------------------------------------------
import csv
import difflib
import io
import json
import math
import re
import urllib.parse
import urllib.request

LAT_MIN, LAT_MAX = -11.5, 6.5      # batas kasar wilayah Indonesia
LON_MIN, LON_MAX = 94.5, 141.5

STATUS_INFO = {
    "I": ("Diizinkan", "Kegiatan boleh dilakukan di zona ini."),
    "T": ("Terbatas", "Boleh, tetapi dibatasi (misalnya luas, jumlah, jam operasional, atau intensitas)."),
    "B": ("Bersyarat", "Boleh jika memenuhi syarat tertentu (misalnya kajian atau persetujuan tambahan)."),
    "X": ("Dilarang", "Kegiatan tidak boleh dilakukan di zona ini."),
}


def _in_id(lat, lon):
    return LAT_MIN <= lat <= LAT_MAX and LON_MIN <= lon <= LON_MAX


def _validate(lat, lon):
    if _in_id(lat, lon):
        return lat, lon
    if _in_id(lon, lat):          # pengguna tertukar urutan (lon, lat)
        return lon, lat
    raise ValueError(f"Koordinat ({lat}, {lon}) berada di luar wilayah Indonesia.")


def parse_coords(text):
    """Ambil (lat, lon) dari teks bebas, JSON, atau link Google Maps."""
    if text is None:
        raise ValueError("Lokasi kosong.")
    s = str(text).strip()

    # 1) JSON: {"lat": .., "lon"/"lng": ..}
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            lat = float(obj.get("lat", obj.get("latitude")))
            lon = float(obj.get("lon", obj.get("lng", obj.get("longitude"))))
            return _validate(lat, lon)
        except (ValueError, TypeError, AttributeError):
            pass

    # 2) Pola link Google Maps
    patterns = [
        r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)",          # data=!3dLAT!4dLON (paling akurat)
        r"@(-?\d+\.\d+),\s*(-?\d+\.\d+)",           # /@LAT,LON,17z
        r"[?&](?:q|ll|query|destination)=(-?\d+\.\d+),\s*(-?\d+\.\d+)",
    ]
    for p in patterns:
        m = re.search(p, s)
        if m:
            return _validate(float(m.group(1)), float(m.group(2)))

    # 3) Dua angka desimal pertama di teks
    nums = re.findall(r"-?\d+\.\d+", s)
    if len(nums) >= 2:
        return _validate(float(nums[0]), float(nums[1]))

    raise ValueError(
        "Koordinat tidak ditemukan. Gunakan format '-6.2088, 106.8456' "
        "atau tempel link Google Maps yang berisi koordinat."
    )


_REMOTE_CACHE = {}


def _read_source(path, encoding="utf-8"):
    """Baca isi file dari path lokal ATAU URL (http/https, mis. raw GitHub).
    Hasil URL di-cache 10 menit supaya tidak mengunduh ulang tiap pemanggilan."""
    import time
    p = str(path).strip()
    if p.lower().startswith(("http://", "https://")):
        hit = _REMOTE_CACHE.get(p)
        if hit and time.time() - hit[0] < 600:
            return hit[1]
        req = urllib.request.Request(p, headers={"User-Agent": "ZonaUsaha/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read().decode(encoding)
        except Exception as e:
            raise ValueError(f"Gagal mengunduh data dari URL ({e}). Cek link raw GitHub-nya.")
        _REMOTE_CACHE[p] = (time.time(), raw)
        return raw
    with open(p, encoding=encoding) as f:
        return f.read()


def load_geojson(text=None, path=None):
    if text and str(text).strip():
        return json.loads(text)
    if path and str(path).strip():
        return json.loads(_read_source(path, "utf-8-sig"))
    raise ValueError("Data GeoJSON zonasi belum diisi (isi teks GeoJSON, path file, atau URL).")


def iter_polygons(geometry):
    """Kembalikan list polygon; tiap polygon = list ring [outer, hole1, ...]."""
    if not geometry:
        return []
    t = geometry.get("type")
    c = geometry.get("coordinates", [])
    if t == "Polygon":
        return [c]
    if t == "MultiPolygon":
        return list(c)
    return []


def _in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / ((yj - yi) or 1e-15) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_polygon(lat, lon, polygon):
    """polygon = [outer_ring, hole_ring, ...]; koordinat GeoJSON = [lon, lat]."""
    if not polygon or not _in_ring(lon, lat, polygon[0]):
        return False
    return not any(_in_ring(lon, lat, hole) for hole in polygon[1:])


def haversine_m(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _to_xy(lat, lon, lat0, lon0):
    x = (lon - lon0) * math.cos(math.radians(lat0)) * 111320.0
    y = (lat - lat0) * 110540.0
    return x, y


def distance_to_polygon_m(lat, lon, polygon):
    """0 jika titik di dalam polygon; selain itu jarak ke tepi terdekat (meter)."""
    if point_in_polygon(lat, lon, polygon):
        return 0.0
    best = float("inf")
    for ring in polygon:
        for k in range(len(ring) - 1):
            ax, ay = _to_xy(ring[k][1], ring[k][0], lat, lon)
            bx, by = _to_xy(ring[k + 1][1], ring[k + 1][0], lat, lon)
            dx, dy = bx - ax, by - ay
            seg = dx * dx + dy * dy
            t = 0.0 if seg == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / seg))
            best = min(best, math.hypot(ax + t * dx, ay + t * dy))
    return best


def polygon_centroid(polygon):
    ring = polygon[0]
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    n = max(len(pts), 1)
    return sum(p[1] for p in pts) / n, sum(p[0] for p in pts) / n


def load_itbx(text=None, path=None):
    if text and str(text).strip():
        raw = str(text)
    elif path and str(path).strip():
        raw = _read_source(path, "utf-8-sig")
    else:
        raise ValueError("Tabel ITBX (CSV) belum diisi (isi teks CSV, path file, atau URL).")
    rows = list(csv.DictReader(io.StringIO(raw.strip())))
    for r in rows:
        r["status"] = (r.get("status") or "").strip().upper()
        r["kode_zona"] = (r.get("kode_zona") or "").strip()
        r["kegiatan"] = (r.get("kegiatan") or "").strip()
    return rows


def norm(s):
    s = str(s or "").lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def activity_names(rows):
    names = {}
    for r in rows:
        keg = r.get("kegiatan")
        if not keg:
            continue
        aliases = [a for a in (r.get("alias") or "").split(";") if a.strip()]
        names.setdefault(keg, set()).update([norm(keg)] + [norm(a) for a in aliases])
    return names


def match_activity(query, rows):
    """Cocokkan cerita pengguna ('laundry kiloan') ke nama kegiatan resmi di CSV.
    Return (nama_kegiatan | None, saran[])."""
    q = norm(query)
    if not q:
        return None, []
    names = activity_names(rows)
    for keg, keys in names.items():                       # 1) sama persis
        if q in keys:
            return keg, []
    best, best_len = None, 0                              # 2) alias terpanjang yang terkandung
    for keg, keys in names.items():
        for k in keys:
            if k and (f" {k} " in f" {q} ") and len(k) > best_len:
                best, best_len = keg, len(k)
    if best:
        return best, []
    lookup = {k: keg for keg, keys in names.items() for k in keys}
    close = difflib.get_close_matches(q, list(lookup), n=1, cutoff=0.8)   # 3) fuzzy
    if close:
        return lookup[close[0]], []
    sugg = difflib.get_close_matches(q, list(lookup), n=5, cutoff=0.4)
    return None, sorted({lookup[s] for s in sugg})


def split_pair(text, key_a, key_b):
    """'A | B' atau JSON {key_a:.., key_b:..} -> (A, B)."""
    s = str(text or "").strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
            return str(obj.get(key_a, "")).strip(), str(obj.get(key_b, "")).strip()
        except (ValueError, AttributeError):
            pass
    if "|" in s:
        a, b = s.split("|", 1)
        return a.strip(), b.strip()
    return s, ""


def json_line(obj):
    return "\nDATA_JSON: " + json.dumps(obj, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Geocoding alamat -> koordinat (Nominatim / OpenStreetMap, gratis tanpa API key)
# Kebijakan Nominatim: maks. ~1 permintaan/detik, wajib User-Agent yang jelas.
# ---------------------------------------------------------------------------
_GEOCODE_CACHE = {}


def geocode_address(address, user_agent, suffix="", timeout=10):
    q = str(address or "").strip()
    if suffix and suffix.strip().lower().strip(", ") not in q.lower():
        q = q + suffix
    if q in _GEOCODE_CACHE:
        return _GEOCODE_CACHE[q]
    params = urllib.parse.urlencode(
        {"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "id"})
    req = urllib.request.Request(
        "https://nominatim.openstreetmap.org/search?" + params,
        headers={"User-Agent": user_agent or "ZonaUsaha-Hackathon/1.0",
                 "Accept-Language": "id"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # jaringan putus, timeout, diblokir, dll.
        raise ValueError(f"Layanan pencarian alamat tidak bisa dihubungi ({e}). "
                         "Coba lagi, atau kirim koordinat/link Google Maps.")
    if not data:
        raise ValueError(f"Alamat '{address}' tidak ditemukan. Tulis lebih lengkap "
                         "(nama jalan, kelurahan, kota) atau kirim link Google Maps.")
    lat, lon = _validate(float(data[0]["lat"]), float(data[0]["lon"]))
    result = (lat, lon, data[0].get("display_name", q))
    _GEOCODE_CACHE[q] = result
    return result


def resolve_location(text, enable_geocoding=True, user_agent="", suffix=""):
    """Return (lat, lon, alamat_dikenali | None, sumber: 'koordinat' | 'alamat')."""
    try:
        lat, lon = parse_coords(text)
        return lat, lon, None, "koordinat"
    except ValueError:
        if enable_geocoding and re.search(r"[A-Za-z]{3,}", str(text or "")):
            lat, lon, name = geocode_address(text, user_agent, suffix)
            return lat, lon, name, "alamat"
        raise


# ===========================================================================
# KOMPONEN: ZonaUsaha - Buat Peta Hasil
# Input  : "lokasi | jenis usaha"  (lokasi boleh alamat, koordinat, atau link Google Maps)
# Output : file HTML peta interaktif (Leaflet) + Message berisi link file & DATA_JSON
# Warna zona = status kegiatan tsb di zona itu (hijau I, kuning T/B, merah X, abu-abu ?)
# ===========================================================================
import datetime
import os
import tempfile

MAP_COLORS = {"I": "#2E7D4F", "T": "#C98A0B", "B": "#C98A0B", "X": "#B83227", "?": "#8A949C"}

MAP_TEMPLATE = """<!doctype html>
<html lang="id"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Peta ZonaUsaha - __TITLE__</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<style>
 html,body{margin:0;height:100%;font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#1D2B36}
 #map{position:absolute;inset:0}
 .card{position:absolute;z-index:500;left:12px;top:12px;max-width:340px;background:#fff;border-radius:12px;
   padding:14px 16px;box-shadow:0 4px 18px rgba(29,43,54,.18);font-size:14px;line-height:1.45}
 .card h1{font-size:17px;margin:0 0 6px}
 .badge{display:inline-block;color:#fff;border-radius:999px;padding:2px 10px;font-weight:700;font-size:13px}
 .legend{position:absolute;z-index:500;right:12px;bottom:26px;background:#fff;border-radius:10px;padding:10px 12px;
   font-size:12.5px;box-shadow:0 2px 10px rgba(29,43,54,.15)}
 .legend div{display:flex;gap:8px;align-items:center;margin:3px 0}
 .sw{width:14px;height:14px;border-radius:3px;opacity:.8}
 .small{color:#5E6B74;font-size:12px;margin-top:8px}
 .pin{width:22px;height:22px;border-radius:50% 50% 50% 0;background:#1D2B36;border:3px solid #fff;transform:rotate(-45deg);box-shadow:0 2px 6px rgba(0,0,0,.35)}
 @media (max-width:600px){.card{right:12px;max-width:none}}
</style></head><body>
<div id="map"></div>
<div class="card">
  <h1>__HEADLINE__</h1>
  <div><span class="badge" style="background:__BADGE_COLOR__">__BADGE__</span></div>
  <div style="margin-top:8px">__DETAIL__</div>
  <div class="small">__DISCLAIMER__</div>
</div>
<div class="legend">
  <div><span class="sw" style="background:#2E7D4F"></span>Diizinkan (I)</div>
  <div><span class="sw" style="background:#C98A0B"></span>Terbatas / Bersyarat (T/B)</div>
  <div><span class="sw" style="background:#B83227"></span>Dilarang (X)</div>
  <div><span class="sw" style="background:#8A949C"></span>Tidak ada data</div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
const ZONES = __GEOJSON__;
const POINT = __POINT__;
const ALTS = __ALTS__;
const map = L.map("map", {zoomControl: false}).setView(POINT, 17);
L.control.zoom({position: "bottomleft"}).addTo(map);
const BASEMAPS = {
  "Peta jalan": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
    {maxZoom: 19, attribution: "Tiles &copy; Esri"}),
  "Satelit": L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {maxZoom: 19, attribution: "Tiles &copy; Esri"})
};
BASEMAPS["Peta jalan"].addTo(map);
L.control.layers(BASEMAPS, null, {position: "topright"}).addTo(map);
// Jika peta jalan gagal dimuat, pindah otomatis ke citra satelit.
let tileErrors = 0;
BASEMAPS["Peta jalan"].on("tileerror", () => {
  if (++tileErrors === 4 && map.hasLayer(BASEMAPS["Peta jalan"])) {
    map.removeLayer(BASEMAPS["Peta jalan"]); BASEMAPS["Satelit"].addTo(map);
  }
});
const layer = L.geoJSON(ZONES, {
  style: f => ({color: "#1D2B36", weight: 1, fillColor: f.properties._color, fillOpacity: .45}),
  onEachFeature: (f, l) => l.bindTooltip(
    `${f.properties._kode} · ${f.properties._nama}<br>Status: ${f.properties._status}`, {sticky: true})
}).addTo(map);
L.marker(POINT, {icon: L.divIcon({className: "", html: '<div class="pin"></div>', iconSize: [22, 22], iconAnchor: [11, 22]})})
  .addTo(map).bindPopup("Lokasi yang dicek").openPopup();
ALTS.forEach((a, i) => L.circleMarker(a.titik_tengah, {radius: 10, color: "#2E7D4F", weight: 3, fillOpacity: .25})
  .addTo(map).bindTooltip(`Alternatif ${i + 1}: ${a.kode_zona} (± ${a.jarak_m} m)`, {permanent: true, direction: "top"}));
try { map.fitBounds(layer.getBounds().extend(POINT), {padding: [30, 30]}); } catch (e) {}
</script></body></html>
"""


def _esc(s):
    return (str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


class ZoneMapComponent(Component):
    display_name = "ZonaUsaha - Buat Peta Hasil"
    description = "Membuat peta interaktif (HTML) berwarna sesuai status izin sebuah usaha di setiap zona."
    icon = "map"
    name = "ZoneMap"

    inputs = [
        MessageTextInput(
            name="query",
            display_name="Lokasi | Kegiatan",
            info="Contoh: 'Jl. Contoh No. 1, Kebayoran Baru | laundry' atau '-6.2025, 106.8005 | bengkel'.",
            tool_mode=True,
        ),
        MultilineInput(name="geojson_text", display_name="GeoJSON Zonasi (teks)", advanced=True),
        StrInput(name="geojson_path", display_name="Path / URL GeoJSON"),
        MultilineInput(name="csv_text", display_name="Tabel ITBX (teks CSV)", advanced=True),
        StrInput(name="csv_path", display_name="Path / URL CSV ITBX"),
        StrInput(
            name="output_dir",
            display_name="Folder Simpan Peta",
            info="Kosongkan untuk memakai folder sementara sistem.",
        ),
        StrInput(
            name="web_map_base_url",
            display_name="URL Website ZONA.U",
            info="Opsional. Contoh: https://zonau.onrender.com . Jika diisi, hasil berisi link peta di website (untuk WhatsApp/web).",
        ),
        StrInput(name="zone_code_field", display_name="Kolom Kode Zona", value="kode_zona", advanced=True),
        StrInput(name="zone_name_field", display_name="Kolom Nama Zona", value="nama_zona", advanced=True),
        IntInput(name="radius_m", display_name="Radius Tampilan (m)", value=1500,
                 info="Hanya zona dalam radius ini yang digambar agar file ringan."),
        BoolInput(name="enable_geocoding", display_name="Terima Input Alamat", value=True),
        StrInput(name="geocode_suffix", display_name="Tambahan Alamat", value=", Jakarta", advanced=True),
        StrInput(name="user_agent", display_name="User-Agent Geocoding",
                 value="ZonaUsaha-Hackathon/1.0 (ganti-dengan-email@anda.com)", advanced=True),
        StrInput(name="data_version", display_name="Versi Data",
                 value="RDTR DKI Jakarta (Pergub 31/2022) - DATA DEMO", advanced=True),
    ]

    outputs = [Output(display_name="Peta Hasil", name="result", method="build_result")]

    def build_result(self) -> Message:
        loc_text, activity = split_pair(self.query, "lokasi", "kegiatan")
        try:
            lat, lon, found_address, loc_source = resolve_location(
                loc_text, self.enable_geocoding, self.user_agent, self.geocode_suffix)
            gj = load_geojson(self.geojson_text, self.geojson_path)
            rows = load_itbx(self.csv_text, self.csv_path)
        except ValueError as e:
            self.status = str(e)
            return Message(text=f"GAGAL: {e}\n" + json_line({"ok": False, "error": str(e)}))

        keg = match_activity(activity, rows)[0] if activity else None
        status_by_zone = {r["kode_zona"]: r["status"] for r in rows if keg and r["kegiatan"] == keg}

        feats, here, alts = [], None, []
        for idx, feat in enumerate(gj.get("features", [])):
            props = feat.get("properties", {}) or {}
            code = str(props.get(self.zone_code_field, "?"))
            status = status_by_zone.get(code, "?")
            polys = iter_polygons(feat.get("geometry"))
            dists = [distance_to_polygon_m(lat, lon, p) for p in polys]
            if not dists or min(dists) > self.radius_m:
                continue
            if min(dists) == 0 and here is None:
                here = {"kode_zona": code, "nama_zona": str(props.get(self.zone_name_field, "-")), "status": status}
            elif status == "I":
                p = polys[dists.index(min(dists))]
                c_lat, c_lon = polygon_centroid(p)
                alts.append({"kode_zona": code, "jarak_m": round(min(dists)),
                             "titik_tengah": [round(c_lat, 6), round(c_lon, 6)]})
            feats.append({"type": "Feature", "geometry": feat.get("geometry"), "properties": {
                "_kode": code, "_nama": str(props.get(self.zone_name_field, "-")),
                "_status": status, "_color": MAP_COLORS.get(status, MAP_COLORS["?"])}})
        alts = sorted(alts, key=lambda a: a["jarak_m"])[:3] if here and here["status"] == "X" else []

        label = {"I": "Diizinkan", "T": "Terbatas", "B": "Bersyarat", "X": "Dilarang"}
        if here is None:
            headline, badge, color = "Lokasi di luar data zonasi", "Tidak ada data", MAP_COLORS["?"]
            detail = "Titik tidak berada di bidang zona mana pun pada data yang dimuat."
        else:
            st = here["status"]
            headline = f"{_esc(keg or activity or 'Usaha')} di {_esc(here['kode_zona'])}"
            badge = f"{st} · {label.get(st, 'Tidak ada data')}"
            color = MAP_COLORS.get(st, MAP_COLORS["?"])
            detail = f"Zona: {_esc(here['kode_zona'])} · {_esc(here['nama_zona'])}"
        if found_address:
            detail += f"<br>Alamat dikenali: {_esc(found_address)}"
        if alts:
            detail += "<br>Alternatif terdekat ditandai lingkaran hijau."
        disclaimer = (f"Sumber: {_esc(self.data_version)}. Gambaran awal, bukan keputusan KKPR resmi. "
                      "Keputusan resmi ada di OSS dan DPMPTSP.")

        html = (MAP_TEMPLATE
                .replace("__TITLE__", _esc(keg or activity or "Cek Lokasi"))
                .replace("__HEADLINE__", headline).replace("__BADGE__", _esc(badge))
                .replace("__BADGE_COLOR__", color).replace("__DETAIL__", detail)
                .replace("__DISCLAIMER__", disclaimer)
                .replace("__GEOJSON__", json.dumps({"type": "FeatureCollection", "features": feats}))
                .replace("__POINT__", json.dumps([lat, lon]))
                .replace("__ALTS__", json.dumps(alts)))

        fname = "peta_zonausaha_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S") + ".html"
        path, url = None, None
        try:  # di Langflow cloud folder lokal bisa tidak tersedia; tidak fatal
            out_dir = (self.output_dir or "").strip() or os.path.join(tempfile.gettempdir(), "zonausaha_maps")
            os.makedirs(out_dir, exist_ok=True)
            path = os.path.abspath(os.path.join(out_dir, fname))
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            url = "file:///" + path.replace("\\", "/").lstrip("/")
        except OSError:
            path = None

        web_url = None
        base = (self.web_map_base_url or "").strip().rstrip("/")
        if base:
            web_url = base + "/peta?" + urllib.parse.urlencode(
                {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "usaha": activity or (keg or "")})

        lines = ["PETA HASIL SIAP"]
        if web_url:
            lines.append(f"- Link peta (website): {web_url}")
        if path:
            lines += [f"- File : {path}", f"- Buka : {url}"]
        lines += [
                 f"- Zona digambar: {len(feats)} (radius {self.radius_m} m)",
                 "- Warna: hijau = diizinkan, kuning = terbatas/bersyarat, merah = dilarang, abu-abu = tidak ada data"]
        self.status = f"Peta dibuat: {fname}"
        return Message(text="\n".join(lines) + "\n" + json_line(
            {"ok": True, "map_path": path, "map_url": url, "web_map_url": web_url, "lat": lat, "lon": lon,
             "kegiatan": keg, "zona": here, "alternatif": alts, "alamat_dikenali": found_address}))
