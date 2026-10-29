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
| `data/itbx.csv` | Tabel ITBX gabungan: zona demo (DMY-*) + data asli Kel. Petamburan (R-1, K-1, K-2, RTH-4) dari Smart RDTR Jakarta Satu |
| `data/itbx_petamburan_asli.csv` | Hanya data asli Petamburan (20 kegiatan × 4 subzona) |
| `data/zona_demo_DUMMY.geojson` | Poligon zona **demo** (belum poligon resmi) |

Komponen menerima **path lokal atau URL** (mis. raw GitHub) untuk GeoJSON dan CSV.

## Pengujian
`python tests/test_components.py` (29 skenario, tanpa internet; Langflow di-stub).

## Batasan
Prototipe hackathon. Poligon zona masih data demo; hasil adalah informasi awal, bukan keputusan KKPR. Keputusan resmi ada di OSS dan DPMPTSP.
