# ZONA.U: Cek Zona, Mulai Usaha

AI agent yang membantu pelaku UMKM mengecek apakah jenis usahanya boleh dibuka di suatu lokasi menurut RDTR DKI Jakarta (Pergub DKI 31/2022), sebelum menyewa tempat.

Hackathon Nasional Hacktiv8 × IBM SkillsBuild 2026 · Arrasyid Fachri Fuady & Maulana Malik Ibrahim (Universitas Indonesia)

## Arsitektur
```
IBM Bob (mode ZonaUsaha) ──MCP──┐
Website / WhatsApp ──API──> Langflow: zonausaha_agent ──> 4 tools (flow Langflow)
                                                          ├─ check_zone_at_location      (components/zone_lookup.py)
                                                          ├─ check_activity_permission   (components/itbx_lookup.py + RAG Astra DB)
                                                          ├─ find_nearby_compliant_zones (components/nearby_zones.py)
                                                          └─ create_zone_map             (components/zone_map.py)
```

## Cara kerja singkat
- **Zona:** lokasi diubah ke koordinat (Nominatim/OpenStreetMap), lalu dicek point-in-polygon pada GeoJSON zonasi.
- **Pemetaan jenis usaha:** deterministik lewat kolom `alias` (tabel sinonim) di CSV ITBX: cocok persis → alias terpanjang yang terkandung → fuzzy match (cutoff 0,8). Jika tidak cocok, agent menyatakan data belum tersedia dan menampilkan saran; tidak menebak.
- **Status izin:** diambil langsung dari tabel ITBX (I/T/B/X). LLM hanya menjelaskan syarat dengan RAG atas dokumen Pergub di Astra DB.
- **Alternatif:** zona terdekat (haversine/jarak ke tepi poligon) yang berstatus diizinkan.

## Data
| File | Isi |
|---|---|
| `data/zona_rdtr_pusat.geojson` | **Poligon subzona RDTR resmi** seluruh Kota Jakarta Pusat (5.903 bidang, 22 kode subzona) |
| `data/zona_rdtr_tanahabang.geojson` | Versi ringan: Kecamatan Tanah Abang saja (1.003 bidang) |
| `data/itbx.csv` | Tabel ITBX: data asli R-1, K-1, K-2, RTH-4 dari Smart RDTR Jakarta Satu (+ baris zona demo DMY-* untuk pengujian) |
| `data/itbx_petamburan_asli.csv` | Hanya data asli (20 kegiatan × 4 subzona) |
| `data/zona_demo_DUMMY.geojson` | Poligon demo, hanya untuk tes otomatis |

**Sumber poligon:** data RDTR DKI Jakarta 2022 dari layanan peta Kementerian ATR/BPN (`dki-siap.atrbpn.go.id/server/rest/services/RDTR_2022/MapServer/0`), dasar hukum Pergub DKI 31/2022. Diproses: reproyeksi EPSG:32748 → WGS84, simplifikasi 0,5 m, badan jalan (BJ) dan badan air (BA) dihapus, atribut disederhanakan (`kode_zona`, `nama_zona`, `kelurahan`, `kecamatan`, `kota`, `id_unik`).

Subzona yang belum ada di tabel ITBX (mis. SPU, KT, RTH-7, RTH-8) dijawab agent sebagai "data belum tersedia".

Komponen menerima **path lokal atau URL** (mis. raw GitHub) untuk GeoJSON dan CSV.

## Pengujian
`python tests/test_components.py` (29 skenario, tanpa internet; Langflow di-stub).

## Batasan
Prototipe hackathon. Tabel ITBX baru mencakup 20 kegiatan × 4 subzona; hasil adalah informasi awal, bukan keputusan KKPR. Keputusan resmi ada di OSS dan DPMPTSP.

## Website + bot WhatsApp (folder `app/`)
Backend FastAPI yang melayani website (`/`), API (`/api/cek`), peta (`/peta?lat=&lon=&usaha=`), dan webhook WhatsApp Cloud API (`/webhook`). Backend memanggil flow agent Langflow; peta digambar dengan `components/zone_map.py` yang sama dengan di Langflow.

Jalankan lokal:
```
pip install -r requirements.txt
cp .env.example .env   # isi nilainya, jangan di-commit
uvicorn app.main:app --reload
```
Deploy (Render/Railway): build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, isi environment variable sesuai `.env.example`.
