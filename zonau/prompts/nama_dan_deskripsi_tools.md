# Nama & deskripsi tools MCP (isi di Langflow: project > MCP Server > Edit tools)

Bob memilih tool HANYA dari nama dan deskripsi. Awali deskripsi dengan kata kerja,
sebut format input, dan kapan dipakai.

| Nama flow / tool | Deskripsi (salin) |
|---|---|
| check_zone_at_location | Cek zona tata ruang (RDTR) dari sebuah lokasi. Input: alamat (nama jalan + kelurahan), koordinat "lat, lon", atau link Google Maps. Output: kode zona, nama zona, wilayah, koordinat, alamat yang dikenali, peringatan batas zona, dan baris DATA_JSON. Panggil pertama kali setiap ada pengecekan lokasi usaha. |
| check_activity_permission | Cek status izin (I=diizinkan, T=terbatas, B=bersyarat, X=dilarang) sebuah jenis usaha di zona tertentu dan jelaskan syaratnya dari dokumen peraturan. Input: "KODE_ZONA \| jenis usaha", contoh "DMY-R \| laundry kiloan". Panggil setelah kode zona diketahui. |
| find_nearby_compliant_zones | Cari 3 zona terdekat yang mengizinkan sebuah jenis usaha. Input: "lat, lon \| jenis usaha". Panggil hanya jika status usaha di lokasi awal adalah X (dilarang). |
| create_zone_map | Buat peta interaktif (file HTML) yang mewarnai zona di sekitar lokasi sesuai status izin usaha (hijau diizinkan, kuning terbatas/bersyarat, merah dilarang) dan menandai lokasi serta alternatif. Input: "lat, lon \| jenis usaha". Panggil di akhir setiap pengecekan lokasi. Output berisi path dan link file peta. |
| create_zoning_report | (Opsional) Simpan ringkasan hasil pengecekan menjadi dokumen Google Docs. Input: ringkasan hasil pengecekan. Panggil hanya jika pengguna meminta laporan. |
