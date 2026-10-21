# Reference dataset and labelling

**Ground truth is by construction.** Every pair comes from a *rendered* synthetic UI scene (100 scenes, seed 2026, 1280×720).
A *changed* pair applies one real DOM/CSS mutation; an *unchanged* pair applies only environment-level noise
that no reviewer would call a regression. Nobody labels images by eye and the diff engine takes no part in the labelling.

| | pairs |
|---|---|
| total | 600 (300 changed, 300 unchanged) |
| core tier (headline metrics) | 540 |
| stress tier (reported separately) | 60 |
| calibration split / test split | 300 / 300, split by scene parity so no scene appears in both |

## Changed (label 1) – mutations with a-priori visibility floors

`text_digit` (1 glyph) · `text_word` · `color_bg` (ΔE2000 ≥ 5 on ≥ 400 px²) · `color_text` (ΔE ≥ 10) · `shift` (≥ 2 px) ·
`resize` (≥ 3 px) · `remove` · `add` (16×16 badge) · `font` (size +1 px / weight) · `radius` (+8 px) · `opacity` (≤ 0.85).
A positive is kept only if it is actually visible: ≥ 30 pixels with ΔE ≥ 5 differ (`VISIBLE_PX`, `VISIBLE_DE`).

## Unchanged (label 0) – noise that must not fail a build

`identical`, `subpixel_text` (sub-pixel glyph positioning), `kerning`, `letter_spacing`, `element_translate` and `page_translate`
(sub-pixel offsets), `color_drift` (ΔE below the visibility floor), `composited` (GPU layer rasterisation),
`masked_dynamic` (clock / counter / spinner inside an ignore region), `combined`.

**Tiers.** A noise pair is `core` only if the DOM-measured displacement of every text box is < 1 px (`MAX_NOISE_DISPLACEMENT_PX`).
Otherwise it is `stress`: layout drifted by a pixel or more, which contradicts the 2 px floor of real shift regressions and cannot be
told apart from a regression by any tolerance. Stress pairs are excluded from the headline FP/FN and reported separately.

> **Disclosure.** The displacement criterion, the visible-pixel filter and the stress tier were introduced *after* a first calibration run
> showed inconsistent labels (text drifting 2 px counted as "unchanged" next to "shift ≥ 2 px" positives). They are defined in terms of the
> rendered page, not of the engine's output, but they were chosen after seeing the engine fail on those pairs. A fresh dataset
> (`vrt dataset generate --seed N`) with the same criteria is the proper out-of-sample check.

## Extra sets

* `calibration_noise` (120 pairs) – JPEG, Gaussian sensor noise, bilinear resampling, gamma drift applied to the *calibration* scenes: part of the calibration set.
* `holdout` (180 pairs) – the same four families on the *test* scenes plus **WebP** and a 0.5 px **blur**, which never appear in calibration.
* `sensitivity` – one mutation swept over its magnitude (shift/resize px, colour ΔE) on 12 fresh scenes; produces the detection-limit curves.

## Limits of the evidence

* Synthetic pages render with one browser (Chromium) on one OS; cross-browser behaviour is not measured.
* The noise families are models of real environment noise, not recordings of it. Real CI fleets add font fallback, hinting and
  GPU differences; pin the image (`Dockerfile`) rather than loosen thresholds.
* 117 unchanged test pairs mean a 95 % interval of roughly ±4 pp on the FP rate; see `results/RESULTS.md` for the intervals.

Rebuild: `make dataset` (several minutes on 2 vCPU). Content hash of the shipped dataset is in `manifest.json` (`content_sha256`).
