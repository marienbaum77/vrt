# Performance

Machine: 2 vCPU, Python 3.13.16.

## Engine, calibrated config (screenshot tiled to size, one recoloured block)

| resolution | MP | median ms | peak traced MB |
|---|---|---|---|
| 640×360 | 0.23 | 93 | 30 |
| 1280×720 | 0.92 | 321 | 117 |
| 1920×1080 | 2.07 | 737 | 262 |
| 1920×2160 | 4.15 | 1639 | 523 |
| 1920×4320 | 8.29 | 3319 | 1045 |

## HTTP service, POST /api/v1/compare (1280×720)

| concurrency | req/s | p50 ms | p95 ms | p99 ms | errors | server RSS max MB |
|---|---|---|---|---|---|---|
| 1 | 5.98 | 155.1 | 208.5 | 236.4 | 0 | 149.6 |
| 2 | 10.84 | 171.9 | 211.3 | 223.7 | 0 | 175.1 |
| 4 | 10.73 | 370.1 | 458.8 | 483.3 | 0 | 223.9 |
| 8 | 10.72 | 693.7 | 906.0 | 965.3 | 0 | 298.0 |
| 16 | 10.01 | 1498.7 | 1890.0 | 1899.0 | 0 | 498.1 |
