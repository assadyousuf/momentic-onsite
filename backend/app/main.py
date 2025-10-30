from __future__ import annotations
import os
import json
import yaml
import pathlib
from typing import List, Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS_DIR = pathlib.Path(os.getenv("TESTS_DIR", ROOT / "tests"))

app = FastAPI(title="Momentic Test Repo API", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class TestSummary(BaseModel):
    id: Optional[str]
    name: str
    description: Optional[str] = ""
    filePath: str = Field(..., description="Path relative to repo root")
    createdAt: str
    updatedAt: str
    stepCount: int
    labels: List[str] = []


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat()


def _get_ctime(stat: os.stat_result) -> float:
    # Prefer birth time if available (macOS), fall back to ctime/mtime
    bt = getattr(stat, "st_birthtime", None)
    if bt:
        return float(bt)
    # On Linux, st_ctime is metadata change time; use mtime as a better proxy
    return float(stat.st_mtime)


def _is_test_yaml(path: pathlib.Path) -> bool:
    n = path.name.lower()
    return n.endswith(".yaml") and n.endswith(".test.yaml")


def list_test_files(base: pathlib.Path) -> List[pathlib.Path]:
    if not base.exists():
        return []
    return sorted([p for p in base.rglob("*.test.yaml") if p.is_file()])


def parse_test(path: pathlib.Path) -> Optional[TestSummary]:
    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
    except Exception:
        return None

    # Validate test file
    file_type = (data.get("fileType") or "").lower()
    if "test" not in file_type:
        return None

    steps = data.get("steps")
    stat = path.stat()
    rel = str(path.relative_to(ROOT))
    labels = data.get("labels") or []

    return TestSummary(
        id=data.get("id"),
        name=data.get("name") or path.stem,
        description=data.get("description") or "",
        filePath=rel,
        createdAt=_iso(_get_ctime(stat)),
        updatedAt=_iso(stat.st_mtime),
        stepCount=len(steps) if isinstance(steps, list) else 0,
        labels=labels,
    )


@app.get("/health")
def health():
    return {"ok": True, "testsDir": str(TESTS_DIR)}


@app.get("/api/tests", response_model=List[TestSummary])
def get_tests():
    files = list_test_files(TESTS_DIR)
    tests: List[TestSummary] = []
    for f in files:
        t = parse_test(f)
        if t:
            tests.append(t)
    # Sort by updatedAt desc
    tests.sort(key=lambda x: x.updatedAt, reverse=True)
    return tests
