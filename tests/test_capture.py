import io

import numpy as np
import pytest
from PIL import Image

from capture import CaptureSession, CaptureSpec, discover_volatile_regions
from diff_engine import DiffConfig, compare_images

PAGE = """<!doctype html><meta charset=utf-8><style>
body{margin:0;font:16px sans-serif} .box{width:80px;height:80px;background:#36c;animation:spin 1s linear infinite;margin:20px}
@keyframes spin{from{transform:rotate(0)}to{transform:rotate(360deg)}}
#late{height:40px;background:#c63;display:none} #clock{position:absolute;right:10px;top:10px;width:90px}
</style><div class=box></div><p>Static text</p><div id=late>delayed block</div><span id=clock data-vrt-ignore>--</span>
<script>setTimeout(()=>{document.getElementById('late').style.display='block'},300);
setInterval(()=>{document.getElementById('clock').textContent=new Date().toISOString().slice(11,23)},20);</script>"""

STRICT = DiffConfig.from_dict({"color": {"delta_e": 0}, "geometry": {"radius_px": 0}, "regions": {"min_area_px": 1, "merge_px": 0}})


def spec(tmp_path, **kw):
    p = tmp_path / "page.html"
    p.write_text(PAGE)
    return CaptureSpec(name="p", url=str(p), viewport_width=400, viewport_height=300, mask_selectors=["[data-vrt-ignore]"], **kw)


@pytest.mark.usefixtures("need_browser")
def test_stabilised_captures_are_pixel_identical(tmp_path):
    with CaptureSession() as s:
        a = s.capture(spec(tmp_path))
        b = s.capture(spec(tmp_path))
    assert a.meta["stable"] and a.meta["image_size"] == [400, 300]
    regions = a.regions + b.regions
    assert regions and regions[0]["selector"] == "[data-vrt-ignore]"
    r = compare_images(a.png, b.png, STRICT, [{k: g[k] for k in "xywh"} for g in regions])
    assert r.raw_diff_pixels == 0  # animation frozen, delayed block waited for, clock masked


@pytest.mark.usefixtures("need_browser")
def test_viewport_and_full_page_and_device_scale(tmp_path):
    with CaptureSession() as s:
        full = s.capture(spec(tmp_path, full_page=True))
        dpr = s.capture(spec(tmp_path, device_scale_factor=2))
    assert full.meta["image_size"][0] == 400
    assert dpr.meta["image_size"] == [800, 600]
    assert dpr.regions[0]["x"] > full.regions[0]["x"]  # region coordinates are scaled to image pixels


@pytest.mark.usefixtures("need_browser")
def test_unknown_browser_and_bad_url(tmp_path):
    from capture import CaptureError

    with pytest.raises(CaptureError):
        CaptureSession("netscape")
    with CaptureSession() as s, pytest.raises(Exception):
        s.capture(CaptureSpec(name="x", url=str(tmp_path / "missing.html")))


def test_discover_volatile_regions():
    def png(arr):
        b = io.BytesIO()
        Image.fromarray(arr).save(b, "PNG")
        return b.getvalue()

    base = np.full((100, 200, 3), 255, np.uint8)
    frames = []
    for i in range(3):
        f = base.copy()
        f[10:20, 150:190] = (i * 80, 0, 0)  # a "clock"
        frames.append(png(f))
    regs = discover_volatile_regions(frames)
    assert len(regs) == 1 and regs[0]["x"] <= 150 and regs[0]["x"] + regs[0]["w"] >= 190
    assert discover_volatile_regions(frames[:1]) == []
