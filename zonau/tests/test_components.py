"""
Uji logika komponen ZonaUsaha TANPA menginstal Langflow.
Modul langflow di-stub agar file komponen bisa di-import apa adanya.
Jalankan:  python tests/test_components.py
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---- stub minimal modul langflow ----
class _Input:
    def __init__(self, name, value=None, **kw):
        self.name, self.value = name, value

class Component:
    def __init__(self, **kwargs):
        for inp in self.inputs:
            setattr(self, inp.name, inp.value)
        for k, v in kwargs.items():
            setattr(self, k, v)
        self.status = None

class Message:
    def __init__(self, text=""):
        self.text = text

lf = types.ModuleType("langflow")
m_custom = types.ModuleType("langflow.custom"); m_custom.Component = Component
m_io = types.ModuleType("langflow.io")
for n in ["BoolInput", "IntInput", "MessageTextInput", "MultilineInput", "StrInput"]:
    setattr(m_io, n, _Input)
m_io.Output = lambda **kw: kw
m_schema = types.ModuleType("langflow.schema")
m_msg = types.ModuleType("langflow.schema.message"); m_msg.Message = Message
sys.modules.update({"langflow": lf, "langflow.custom": m_custom, "langflow.io": m_io,
                    "langflow.schema": m_schema, "langflow.schema.message": m_msg})

sys.path.insert(0, os.path.join(ROOT, "components"))
import itbx_lookup, nearby_zones, zone_lookup, zone_map  # noqa: E402
import io, json, tempfile  # noqa: E402

GJ = os.path.join(ROOT, "data", "zona_demo_DUMMY.geojson")
CSV = os.path.join(ROOT, "data", "itbx.csv")
passed = failed = 0

def check(name, cond, out=""):
    global passed, failed
    if cond:
        passed += 1; print(f"  OK   {name}")
    else:
        failed += 1; print(f"  FAIL {name}\n{out}")

print("parse_coords")
pc = zone_lookup.parse_coords
check("teks biasa", pc("-6.2010, 106.8005") == (-6.201, 106.8005))
check("tertukar lon,lat", pc("106.8005, -6.2010") == (-6.201, 106.8005))
check("json", pc('{"lat": -6.2, "lng": 106.8}') == (-6.2, 106.8))
check("link @", pc("https://www.google.com/maps/@-6.2010,106.8005,17z") == (-6.201, 106.8005))
check("link !3d!4d", pc("https://maps.google.com/place/X/data=!3d-6.2011!4d106.8006") == (-6.2011, 106.8006))
try:
    pc("halo"); check("error tanpa angka", False)
except ValueError:
    check("error tanpa angka", True)

print("ZoneLookup")
z = lambda loc: zone_lookup.ZoneLookupComponent(location_text=loc, geojson_path=GJ).build_result().text
out = z("-6.2025, 106.8005"); check("zona perumahan", "DMY-R" in out, out)
out = z("-6.2015, 106.8015"); check("lubang polygon -> taman", "DMY-H" in out and "Kode zona : DMY-R" not in out, out)
out = z("-6.2010, 106.8045"); check("zona perdagangan", "DMY-K" in out, out)
out = z("-6.20296, 106.8010"); check("peringatan dekat batas", "PERINGATAN" in out, out)
out = z("-6.3000, 106.9000"); check("di luar data", "TIDAK DITEMUKAN" in out, out)

print("ItbxLookup")
t = lambda q: itbx_lookup.ItbxLookupComponent(query=q, csv_path=CSV).build_result().text
out = t("DMY-R | laundry kiloan"); check("alias laundry di R -> T", "Status    : T" in out, out)
out = t("DMY-R | saya mau buka bengkel motor"); check("kalimat panjang bengkel di R -> X", "Status    : X" in out, out)
out = t('{"kode_zona":"DMY-K","kegiatan":"coffee shop"}'); check("json kafe di K -> I", "Status    : I" in out, out)
out = t("DMY-R | jual mobil bekas"); check("kegiatan tak dikenal -> tidak menebak", "TIDAK DITEMUKAN" in out, out)
out = t("ZZZ | laundry"); check("zona tak dikenal", "zone_not_found" in out, out)
out = t("laundry saja"); check("format salah", "GAGAL" in out, out)

print("NearbyZones")
n = lambda q, **kw: nearby_zones.NearbyZonesComponent(query=q, geojson_path=GJ, csv_path=CSV, **kw).build_result().text
out = n("-6.2025, 106.8005 | bengkel"); check("bengkel dari R -> alternatif K", "DMY-K" in out and "1." in out, out)
out = n("-6.2025, 106.8005 | bengkel", include_limited=True); check("sertakan B -> C muncul", "DMY-C" in out, out)
out = n("-6.2025, 106.8005 | jual mobil"); check("kegiatan tak dikenal", "TIDAK DITEMUKAN" in out, out)


print("Geocoding alamat (Nominatim di-mock, tanpa internet)")
class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False
def fake_urlopen(payload):
    def _f(req, timeout=10):
        fake_urlopen.last_url = req.full_url
        return _Resp(json.dumps(payload).encode())
    return _f
for mod in (zone_lookup, zone_map):
    mod._GEOCODE_CACHE.clear()
zone_lookup.urllib.request.urlopen = fake_urlopen([{"lat": "-6.2025", "lon": "106.8005", "display_name": "Jalan Contoh, Kelurahan Contoh"}])
out = z("Jalan Contoh No. 5")
check("alamat -> zona DMY-R", "DMY-R" in out and "Alamat dikenali" in out and "dari alamat" in out, out)
check("suffix kota ditambahkan & countrycodes=id", "Jakarta" in fake_urlopen.last_url and "countrycodes=id" in fake_urlopen.last_url)
zone_lookup._GEOCODE_CACHE.clear()
zone_lookup.urllib.request.urlopen = fake_urlopen([])
out = z("Jalan Yang Tidak Ada Sama Sekali")
check("alamat tidak ditemukan -> GAGAL jelas", "GAGAL" in out and "tidak ditemukan" in out, out)
out = zone_lookup.ZoneLookupComponent(location_text="Jalan Contoh", geojson_path=GJ, enable_geocoding=False).build_result().text
check("geocoding dimatikan -> minta koordinat", "GAGAL" in out and "Koordinat tidak ditemukan" in out, out)

print("ZoneMap (peta HTML)")
tmp = tempfile.mkdtemp()
m = lambda q: zone_map.ZoneMapComponent(query=q, geojson_path=GJ, csv_path=CSV, output_dir=tmp).build_result().text
out = m("-6.2025, 106.8005 | bengkel motor")
d = json.loads(out.split("DATA_JSON: ")[1])
check("file peta dibuat", os.path.exists(d["map_path"]), out)
html = open(d["map_path"], encoding="utf-8").read()
check("status X + 2 alternatif di peta", d["zona"]["status"] == "X" and len(d["alternatif"]) == 2, out)
check("warna merah & hijau tertanam", "#B83227" in html and "#2E7D4F" in html)
check("tidak ada placeholder tersisa", "__" not in html.split("<script>")[0] and "__GEOJSON__" not in html)
out = m("-6.3000, 106.9000 | laundry")
check("peta titik di luar data", "Lokasi di luar data zonasi" in open(json.loads(out.split("DATA_JSON: ")[1])["map_path"], encoding="utf-8").read(), out)

print(f"\n{passed} lulus, {failed} gagal")
sys.exit(1 if failed else 0)
