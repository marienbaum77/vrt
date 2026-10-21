# Capture-stabilisation ablation

5 unstable page kinds × 4 variants × 5 repeat captures; every pair is *unchanged* by construction, so each flagged pair is a false positive.

| page kind | naive | stable | masked | auto |
|---|---|---|---|---|
| animation | 10/20 | 0/20 | 0/20 | 0/20 |
| delayed_content | 0/20 | 0/20 | 0/20 | 0/20 |
| slow_image | 3/20 | 0/20 | 0/20 | 0/20 |
| clock | 17/20 | 0/20 | 0/20 | 0/20 |
| live_value | 20/20 | 16/20 | 0/20 | 5/20 |
| **all** | **50.0%** (50/100) | **16.0%** (16/100) | **0.0%** (0/100) | **5.0%** (5/100) |

Regression control (real change outside the masked element): masked: 20/20 detected; stable: 20/20 detected
