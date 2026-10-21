"""Baseline storage: ``<root>/<browser>/<viewport>/<scenario>.png`` plus a ``.meta.json`` sidecar."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass(frozen=True)
class PageKey:
    scenario: str
    browser: str
    viewport: str

    @property
    def page_id(self) -> str:
        return f"{self.scenario}@{self.browser}/{self.viewport}"

    @property
    def slug(self) -> str:
        return f"{self.scenario}.{self.browser}.{self.viewport}"

    @property
    def rel(self) -> Path:
        return Path(self.browser) / self.viewport / f"{self.scenario}.png"


class BaselineStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def png_path(self, key: PageKey) -> Path:
        return self.root / key.rel

    def meta_path(self, key: PageKey) -> Path:
        return self.png_path(key).with_suffix(".meta.json")

    def exists(self, key: PageKey) -> bool:
        return self.png_path(key).is_file()

    def load(self, key: PageKey) -> Tuple[bytes, Dict[str, Any]]:
        png = self.png_path(key).read_bytes()
        mp = self.meta_path(key)
        meta = json.loads(mp.read_text()) if mp.is_file() else {}
        return png, meta

    def save(self, key: PageKey, png: bytes, meta: Optional[Dict[str, Any]] = None) -> Path:
        p = self.png_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(png)
        self.meta_path(key).write_text(json.dumps(meta or {}, indent=2, ensure_ascii=False))
        return p

    def keys(self) -> List[PageKey]:
        out: List[PageKey] = []
        if not self.root.is_dir():
            return out
        for p in sorted(self.root.glob("*/*/*.png")):
            out.append(PageKey(p.stem, p.parent.parent.name, p.parent.name))
        return out

    def approve_from(self, candidates_dir: str | Path, only: Optional[List[str]] = None) -> List[PageKey]:
        """Promote captured candidates (``<dir>/<browser>/<viewport>/<scenario>.png``) to baselines."""
        src = BaselineStore(candidates_dir)
        done: List[PageKey] = []
        for key in src.keys():
            if only and key.scenario not in only:
                continue
            dst = self.png_path(key)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src.png_path(key), dst)
            mp = src.meta_path(key)
            if mp.is_file():
                shutil.copyfile(mp, self.meta_path(key))
            done.append(key)
        return done
