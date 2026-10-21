# Verification results

Dataset `vrt-synthetic-ui` (seed 2026, sha256 `62ef0950deb278fb…`): 600 pairs, 540 core + 60 stress. Thresholds are calibrated on the calibration split only; all numbers below are on the held-out **test split, core tier** unless stated otherwise. Intervals are 95 % Wilson (rates) / bootstrap (AUC).

**Operating point:** `radius_px=1`, `blur_sigma_px=2.0`, `blur ΔE=5.0`, `merge_px=3`, `min_area_px=8`, `fail_pixels=9.4`.

## Accuracy of the compared algorithms (test split)

| algorithm | accuracy | FP rate | FN rate | AUC | threshold |
|---|---|---|---|---|---|
| S0 exact pixel diff | 43.4% [37.6%–49.4%] | 31.6% [23.9%–40.5%] | 76.0% [68.6%–82.1%] | 0.612 [0.534–0.688] | 7936 |
| S1 + colour tolerance (dE2000) | 55.8% [49.8%–61.6%] | 30.8% [23.1%–39.6%] | 54.7% [46.7%–62.4%] | 0.707 [0.632–0.770] | 1552 |
| S2 + geometry tolerance (r px, blend) | 65.9% [60.0%–71.3%] | 17.9% [12.0%–25.9%] | 46.7% [38.9%–54.6%] | 0.836 [0.783–0.888] | 507.5 |
| S3 + anti-aliasing low-pass | 95.1% [91.8%–97.1%] | 6.0% [2.9%–11.8%] | 4.0% [1.8%–8.5%] | 0.963 [0.935–0.985] | 10.49 |
| S4 + region filter (VRT) | 95.5% [92.3%–97.4%] | 5.1% [2.4%–10.7%] | 4.0% [1.8%–8.5%] | 0.960 [0.932–0.982] | 9.392 |
| PSNR | 66.7% [60.8%–72.0%] | 28.2% [20.8%–37.0%] | 37.3% [30.0%–45.3%] | 0.790 [0.728–0.843] | -41.59 |
| SSIM (mean) | 58.8% [52.8%–64.5%] | 26.5% [19.3%–35.1%] | 52.7% [44.7%–60.5%] | 0.745 [0.678–0.805] | 0.001335 |
| SSIM (0.1 % percentile) | 69.3% [63.5%–74.5%] | 30.8% [23.1%–39.6%] | 30.7% [23.8%–38.5%] | 0.794 [0.736–0.841] | 0.1224 |
| pHash (Hamming) | 58.1% [52.1%–63.8%] | 2.6% [0.9%–7.3%] | 72.7% [65.0%–79.2%] | 0.625 [0.589–0.663] | 0.7321 |

McNemar exact test, calibrated VRT vs `ssim_low` (best global baseline): only VRT correct on 72 pairs, only the other on 2, p = 2.9e-19.

McNemar exact test, calibrated VRT vs `pixel_geometry` (without antialiasing stage): only VRT correct on 82 pairs, only the other on 3, p = 5.3e-21.

Calibration split: FP 4.4%, FN 4.8%; 5-fold CV inside it (selection optimism check): accuracy 94.9% ± 2.1%.

## Image-space noise on the test scenes (holdout)

Four families (JPEG, Gaussian, resample, gamma) have counterparts on the calibration scenes (*seen*); WebP and 0.5 px blur occur only here (*unseen*).

| noise | seen in calibration | FP / n | FP rate [95 % CI] | regressions detected (recall) |
|---|---|---|---|---|
| jpeg | yes | 5/15 | 33.3% [15.2%–58.3%] | 14/15 (93.3%) |
| gauss | yes | 1/15 | 6.7% [1.2%–29.8%] | 15/15 (100.0%) |
| resample | yes | 1/15 | 6.7% [1.2%–29.8%] | 15/15 (100.0%) |
| gamma | yes | 0/15 | 0.0% [0.0%–20.4%] | 14/15 (93.3%) |
| webp | no | 3/15 | 20.0% [7.0%–45.2%] | 15/15 (100.0%) |
| blur | no | 0/15 | 0.0% [0.0%–20.4%] | 15/15 (100.0%) |

Overall holdout: FP 11.1% [6.1%–19.3%], FN 2.2% [0.6%–7.7%].

## Stress tier (unchanged by intent, layout drifted ≥ 1 px)

35/60 flagged (58.3% [45.7%–69.9%]). Font-metric drift above one pixel contradicts the 2 px floor of real shift regressions; it is fixed by pinning the rendering environment, not by tolerances.

## Errors by category (calibration + test, core and stress)

| class | category | tier | n | errors | rate [95 % CI] |
|---|---|---|---|---|---|
| unchanged | color_drift | core | 24 | 0 | 0.0% [0.0%–13.8%] |
| unchanged | combined | core | 14 | 2 | 14.3% [4.0%–39.9%] |
| unchanged | combined | stress | 10 | 7 | 70.0% [39.7%–89.2%] |
| unchanged | composited | core | 19 | 1 | 5.3% [0.9%–24.6%] |
| unchanged | element_translate | core | 48 | 6 | 12.5% [5.9%–24.7%] |
| unchanged | gamma | core | 15 | 0 | 0.0% [0.0%–20.4%] |
| unchanged | gauss | core | 15 | 0 | 0.0% [0.0%–20.4%] |
| unchanged | identical | core | 24 | 0 | 0.0% [0.0%–13.8%] |
| unchanged | jpeg | core | 15 | 4 | 26.7% [10.9%–52.0%] |
| unchanged | kerning | core | 7 | 0 | 0.0% [0.0%–35.4%] |
| unchanged | kerning | stress | 17 | 10 | 58.8% [36.0%–78.4%] |
| unchanged | letter_spacing | core | 24 | 0 | 0.0% [0.0%–13.8%] |
| unchanged | masked_dynamic | core | 47 | 0 | 0.0% [0.0%–7.6%] |
| unchanged | page_translate | core | 18 | 1 | 5.6% [1.0%–25.8%] |
| unchanged | resample | core | 15 | 0 | 0.0% [0.0%–20.4%] |
| unchanged | subpixel_text | core | 15 | 0 | 0.0% [0.0%–20.4%] |
| unchanged | subpixel_text | stress | 33 | 18 | 54.5% [38.0%–70.2%] |
| changed | add | core | 16 | 0 | 0.0% [0.0%–19.4%] |
| changed | add+gamma | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | add+jpeg | core | 2 | 0 | 0.0% [0.0%–65.8%] |
| changed | color_bg | core | 46 | 1 | 2.2% [0.4%–11.3%] |
| changed | color_bg+gamma | core | 2 | 0 | 0.0% [0.0%–65.8%] |
| changed | color_bg+gauss | core | 2 | 0 | 0.0% [0.0%–65.8%] |
| changed | color_bg+jpeg | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | color_bg+resample | core | 2 | 0 | 0.0% [0.0%–65.8%] |
| changed | color_text | core | 26 | 0 | 0.0% [0.0%–12.9%] |
| changed | color_text+gamma | core | 3 | 0 | 0.0% [0.0%–56.1%] |
| changed | color_text+gauss | core | 2 | 0 | 0.0% [0.0%–65.8%] |
| changed | color_text+jpeg | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | color_text+resample | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | font | core | 25 | 0 | 0.0% [0.0%–13.3%] |
| changed | font+gamma | core | 2 | 0 | 0.0% [0.0%–65.8%] |
| changed | font+gauss | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | font+resample | core | 3 | 0 | 0.0% [0.0%–56.1%] |
| changed | opacity | core | 13 | 0 | 0.0% [0.0%–22.8%] |
| changed | opacity+gamma | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | opacity+gauss | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | opacity+resample | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | radius | core | 12 | 6 | 50.0% [25.4%–74.6%] |
| changed | radius+gamma | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | radius+resample | core | 1 | 1 | 100.0% [20.7%–100.0%] |
| changed | remove | core | 24 | 0 | 0.0% [0.0%–13.8%] |
| changed | remove+gamma | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | remove+gauss | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | remove+jpeg | core | 2 | 0 | 0.0% [0.0%–65.8%] |
| changed | resize | core | 32 | 0 | 0.0% [0.0%–10.7%] |
| changed | resize+gamma | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | resize+jpeg | core | 5 | 0 | 0.0% [0.0%–43.4%] |
| changed | shift | core | 42 | 1 | 2.4% [0.4%–12.3%] |
| changed | shift+gamma | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | shift+gauss | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | shift+jpeg | core | 2 | 0 | 0.0% [0.0%–65.8%] |
| changed | shift+resample | core | 3 | 0 | 0.0% [0.0%–56.1%] |
| changed | text_digit | core | 24 | 3 | 12.5% [4.3%–31.0%] |
| changed | text_digit+gamma | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | text_digit+gauss | core | 4 | 1 | 25.0% [4.6%–69.9%] |
| changed | text_digit+resample | core | 2 | 1 | 50.0% [9.5%–90.5%] |
| changed | text_word | core | 40 | 1 | 2.5% [0.4%–12.9%] |
| changed | text_word+gamma | core | 1 | 0 | 0.0% [0.0%–79.3%] |
| changed | text_word+gauss | core | 3 | 1 | 33.3% [6.1%–79.2%] |
| changed | text_word+jpeg | core | 2 | 0 | 0.0% [0.0%–65.8%] |
| changed | text_word+resample | core | 2 | 0 | 0.0% [0.0%–65.8%] |

## Detection limits (clean mutations of known size)

**color_bg** (dE2000): 1→0.0%, 1.5→0.0%, 2→0.0%

**color_text** (dE2000): 3→0.0%, 5→0.0%, 8→25.0%, 12→75.0%, 20→100.0%, 40→100.0%

**resize** (px): 1→8.3%, 2→83.3%, 3→100.0%, 5→100.0%, 8→100.0%, 12→100.0%

**shift** (px): 1→16.7%, 2→83.3%, 3→91.7%, 4→100.0%, 6→100.0%, 8→100.0%

## Threshold sensitivity (share of test verdicts that flip)

| parameter change | flips | FP rate | FN rate |
|---|---|---|---|
| color_delta_e+10% | 0.0% | 5.1% | 4.0% |
| color_delta_e-10% | 0.0% | 5.1% | 4.0% |
| blur_sigma_px+10% | 2.2% | 5.1% | 8.0% |
| blur_sigma_px-10% | 0.4% | 6.0% | 4.0% |
| blur_delta_e+10% | 1.5% | 5.1% | 6.7% |
| blur_delta_e-10% | 0.4% | 6.0% | 4.0% |
| radius_px+1 | 6.0% | 3.4% | 13.3% |
| radius_px-1 | 6.7% | 20.5% | 4.0% |
| merge_px+1 | 0.0% | 5.1% | 4.0% |
| merge_px-1 | 0.4% | 5.1% | 4.7% |
| min_area_px+1 | 0.0% | 5.1% | 4.0% |
| min_area_px-1 | 0.0% | 5.1% | 4.0% |
| fail_pixels+10% | 0.0% | 5.1% | 4.0% |
| fail_pixels-10% | 0.0% | 5.1% | 4.0% |

Comparison latency incl. SSIM/PSNR/pHash extras on 1280×720: p50 430 ms, p95 1295 ms.
