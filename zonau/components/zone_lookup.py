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
# KOMPONEN: ZonaUsaha - Cek Zona Lokasi
# Input  : teks berisi koordinat / link Google Maps
# Output : Message (ringkasan + baris DATA_JSON untuk dibaca Bob / UI)
# ===========================================================================
class ZoneLookupComponent(Component):
    display_name = "ZonaUsaha - Cek Zona Lokasi"
    description = "Menentukan zona RDTR dari titik koordinat (point-in-polygon)."
    icon = "map-pin"
    name = "ZoneLookup"

    inputs = [
        MessageTextInput(
            name="location_text",
            display_name="Lokasi",
            info="Alamat, koordinat '-6.2, 106.8', JSON {\"lat\":..,\"lon\":..}, atau link Google Maps.",
            tool_mode=True,
        ),
        MultilineInput(
            name="geojson_text",
            display_name="GeoJSON Zonasi (teks)",
            info="Tempel isi file GeoJSON. Kosongkan jika memakai path.",
            advanced=True,
        ),
        StrInput(
            name="geojson_path",
            display_name="Path / URL GeoJSON",
            info="Path absolut file .geojson di komputer yang menjalankan Langflow.",
        ),
        StrInput(name="zone_code_field", display_name="Kolom Kode Zona", value="kode_zona", advanced=True),
        StrInput(name="zone_name_field", display_name="Kolom Nama Zona", value="nama_zona", advanced=True),
        StrInput(name="area_field", display_name="Kolom Nama Wilayah", value="kelurahan", advanced=True),
        StrInput(
            name="data_version",
            display_name="Versi Data",
            value="RDTR DKI Jakarta (Pergub 31/2022) - DATA DEMO",
            advanced=True,
        ),
        BoolInput(
            name="enable_geocoding",
            display_name="Terima Input Alamat",
            value=True,
            info="Jika input bukan koordinat, cari koordinatnya dari alamat lewat OpenStreetMap (Nominatim).",
        ),
        StrInput(
            name="geocode_suffix",
            display_name="Tambahan Alamat",
            value=", Jakarta",
            info="Ditambahkan ke alamat agar pencarian fokus ke kota demo.",
            advanced=True,
        ),
        StrInput(
            name="user_agent",
            display_name="User-Agent Geocoding",
            value="ZonaUsaha-Hackathon/1.0 (ganti-dengan-email@anda.com)",
            info="Wajib menurut kebijakan Nominatim. Isi dengan nama aplikasi + email Anda.",
            advanced=True,
        ),
        IntInput(
            name="edge_tolerance_m",
            display_name="Toleransi Batas Zona (m)",
            value=10,
            info="Jika titik berjarak < nilai ini dari zona lain, beri peringatan 'dekat batas zona'.",
            advanced=True,
        ),
    ]

    outputs = [Output(display_name="Hasil Cek Zona", name="result", method="build_result")]

    def build_result(self) -> Message:
        try:
            lat, lon, found_address, loc_source = resolve_location(
                self.location_text, self.enable_geocoding, self.user_agent, self.geocode_suffix)
            gj = load_geojson(self.geojson_text, self.geojson_path)
        except ValueError as e:
            self.status = str(e)
            return Message(text=f"GAGAL: {e}\n" + json_line({"ok": False, "error": str(e)}))

        found, near = None, []
        for feat in gj.get("features", []):
            props = feat.get("properties", {}) or {}
            for poly in iter_polygons(feat.get("geometry")):
                d = distance_to_polygon_m(lat, lon, poly)
                if d == 0 and found is None:
                    found = props
                elif 0 < d <= self.edge_tolerance_m:
                    near.append(str(props.get(self.zone_code_field, "?")))

        if found is None:
            msg = ("Titik berada di luar cakupan data zonasi demo "
                   "(atau di jalan/sungai yang tidak memiliki zona usaha).")
            self.status = msg
            extra = f"\n- Alamat dikenali: {found_address}" if found_address else ""
            return Message(text=f"TIDAK DITEMUKAN: {msg}{extra}\n" + json_line(
                {"ok": True, "found": False, "lat": lat, "lon": lon, "sumber_lokasi": loc_source,
                 "alamat_dikenali": found_address, "data_version": self.data_version}))

        code = str(found.get(self.zone_code_field, "?"))
        name = str(found.get(self.zone_name_field, "-"))
        area = str(found.get(self.area_field, "-"))
        near = sorted(set(n for n in near if n != code))
        lines = [
            "HASIL CEK ZONA",
            f"- Koordinat : {lat:.6f}, {lon:.6f} (dari {loc_source})",
            f"- Kode zona : {code}",
            f"- Nama zona : {name}",
            f"- Wilayah   : {area}",
            f"- Sumber    : {self.data_version}",
        ]
        if found_address:
            lines.insert(1, f"- Alamat dikenali: {found_address}")
            lines.append("- CATATAN: koordinat berasal dari pencarian alamat dan bisa meleset "
                         "puluhan meter. Minta pengguna mengonfirmasi titiknya atau mengirim link Google Maps.")
        if near:
            lines.append(f"- PERINGATAN: titik dekat batas zona lain ({', '.join(near)}); "
                         "pastikan titik tepat di lokasi bangunan.")
        data = {"ok": True, "found": True, "lat": lat, "lon": lon, "sumber_lokasi": loc_source,
                "alamat_dikenali": found_address, "kode_zona": code,
                "nama_zona": name, "wilayah": area, "dekat_batas": near,
                "data_version": self.data_version}
        self.status = f"{code} - {name}"
        return Message(text="\n".join(lines) + "\n" + json_line(data))
