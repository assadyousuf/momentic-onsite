from __future__ import annotations
import os
import json
import yaml
import pathlib
from typing import List, Optional
from datetime import datetime
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import hashlib
import requests
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS_DIR = pathlib.Path(os.getenv("TESTS_DIR", ROOT / "tests"))

load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022").strip()

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
    disabled: bool = False


class SummaryResponse(BaseModel):
    summaryMarkdown: str
    model: str
    cached: bool
    contentHash: str


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat()


def _get_ctime(stat: os.stat_result) -> float:
    bt = getattr(stat, "st_birthtime", None)
    if bt:
        return float(bt)
    return float(stat.st_mtime)


def list_test_files(base: pathlib.Path) -> List[pathlib.Path]:
    if not base.exists():
        return []
    return sorted([p for p in base.rglob("*.test.yaml") if p.is_file()])


def _read_yaml(p: pathlib.Path) -> dict:
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def parse_test(path: pathlib.Path) -> Optional[TestSummary]:
    try:
        raw = path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw) or {}
    except Exception:
        return None

    file_type = (data.get("fileType") or "").lower()
    if "test" not in file_type:
        return None

    steps = data.get("steps")
    stat = path.stat()
    rel = str(path.relative_to(ROOT))
    labels = data.get("labels") or []
    disabled = bool(data.get("disabled") or False)

    return TestSummary(
        id=data.get("id"),
        name=data.get("name") or path.stem,
        description=data.get("description") or "",
        filePath=rel,
        createdAt=_iso(_get_ctime(stat)),
        updatedAt=_iso(stat.st_mtime),
        stepCount=len(steps) if isinstance(steps, list) else 0,
        labels=labels,
        disabled=disabled,
    )


def _list_module_files(base: pathlib.Path) -> List[pathlib.Path]:
    if not base.exists():
        return []
    return sorted([p for p in base.rglob("*.module.yaml") if p.is_file()])


def _build_module_index() -> dict:
    idx: dict[str, dict] = {}
    for p in _list_module_files(TESTS_DIR):
        data = _read_yaml(p)
        module_id = data.get("moduleId")
        if not module_id:
            continue
        idx[module_id] = {
            "name": data.get("name") or p.stem,
            "steps": data.get("steps") or [],
            "path": str(p),
            "raw": p.read_text(encoding="utf-8"),
        }
    return idx


def _find_test_file_by_id(test_id: str) -> Optional[pathlib.Path]:
    for p in list_test_files(TESTS_DIR):
        data = _read_yaml(p)
        if str(data.get("id")) == test_id:
            return p
    return None


def _step_synopsis(step: dict) -> str:
    t = (step.get("type") or "").upper()
    if t == "MODULE":
        return "MODULE"
    if t == "PRESET_ACTION":
        cmd = step.get("command") or {}
        ctype = (cmd.get("type") or "").upper()
        bits: List[str] = [ctype] if ctype else []
        target = (cmd.get("target") or {}).get("elementDescriptor")
        if target:
            bits.append(f"target={target}")
        if cmd.get("pressEnter"):
            bits.append("pressEnter")
        if cmd.get("clearContent"):
            bits.append("clearContent")
        if cmd.get("value") is not None:
            v = str(cmd.get("value"))
            if len(v) > 80:
                v = v[:77] + "…"
            bits.append(f"value={v}")
        if cmd.get("assertion"):
            a = str(cmd.get("assertion"))
            if len(a) > 120:
                a = a[:117] + "…"
            bits.append(f"assertion={a}")
        return ", ".join(bits) if bits else "ACTION"
    return t or "STEP"


def _summarize_steps(test: dict, modules_idx: dict, expand: bool = False) -> List[str]:
    res: List[str] = []
    steps = test.get("steps") or []
    for s in steps:
        stype = (s.get("type") or "").upper()
        if stype == "MODULE":
            mid = s.get("moduleId")
            m = modules_idx.get(mid)
            if not m:
                res.append(f"MODULE: {mid} (missing)")
                continue
            mname = m.get("name")
            msteps: List[dict] = m.get("steps") or []
            if not expand:
                preview = ", ".join(_step_synopsis(ms) for ms in msteps[:2])
                if preview:
                    res.append(f"MODULE: {mname} ({len(msteps)} steps) – {preview}")
                else:
                    res.append(f"MODULE: {mname} ({len(msteps)} steps)")
            else:
                res.append(f"MODULE: {mname} ({len(msteps)} steps)")
                for ms in msteps:
                    res.append("  - " + _step_synopsis(ms))
        else:
            res.append(_step_synopsis(s))
    return res


SUMMARY_CACHE: dict[tuple[str, str, str], SummaryResponse] = {}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _content_hash(test_path: pathlib.Path, test: dict, modules_idx: dict) -> str:
    pieces: List[bytes] = []
    pieces.append(test_path.read_bytes())
    for s in test.get("steps") or []:
        if (s.get("type") or "").upper() == "MODULE":
            mid = s.get("moduleId")
            m = modules_idx.get(mid)
            if m and m.get("raw"):
                pieces.append(m["raw"].encode("utf-8"))
    return _sha256_bytes(b"\n".join(pieces))


def _anthropic_summarize(test: dict, synopsis: List[str]) -> str:
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=400, detail="Missing ANTHROPIC_API_KEY in environment")
    system = (
        "You summarize Momentic end-to-end UI tests for developers. "
        "Be concise and factual. Output sections: Purpose, Preconditions, Main Flow, Assertions, Side Effects, Risks."
    )
    name = test.get("name") or "Unnamed test"
    description = test.get("description") or ""
    envs = test.get("envs") or []
    env_str = ", ".join([e.get("name") for e in envs if isinstance(e, dict) and e.get("name")])
    lines = [
        f"Test: {name}",
        f"Description: {description}",
        f"Envs: {env_str}" if env_str else "Envs: (none)",
        "Steps (condensed):",
    ]
    lines += [f"- {x}" for x in synopsis[:120]]
    prompt = "\n".join(lines)

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 600,
        "system": system,
        "messages": [
            {"role": "user", "content": prompt},
        ],
    }
    r = requests.post(url, headers=headers, data=json.dumps(body), timeout=60)
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"Anthropic error: {r.text[:500]}")
    data = r.json()
    content = data.get("content") or []
    text_blocks = [b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text" and b.get("text")]
    summary = "\n\n".join(text_blocks).strip()
    if not summary:
        summary = "(No summary returned)"
    return summary


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
    tests.sort(key=lambda x: x.updatedAt, reverse=True)
    return tests


@app.get("/api/tests/{test_id}/summary", response_model=SummaryResponse)
def get_test_summary(
    test_id: str,
    expand: str = Query("collapsed", enum=["collapsed", "inline"]),
    refresh: bool = False,
):
    test_path = _find_test_file_by_id(test_id)
    if not test_path:
        raise HTTPException(status_code=404, detail="Test not found")

    test_doc = _read_yaml(test_path)
    allow_cache = not bool(((test_doc.get("advanced") or {}).get("disableAICaching")))
    modules_idx = _build_module_index()
    synopsis = _summarize_steps(test_doc, modules_idx, expand == "inline")
    c_hash = _content_hash(test_path, test_doc, modules_idx)
    cache_key = (test_id, c_hash, expand)

    if allow_cache and not refresh and cache_key in SUMMARY_CACHE:
        cached = SUMMARY_CACHE[cache_key]
        return cached

    summary_md = _anthropic_summarize(test_doc, synopsis)
    resp = SummaryResponse(summaryMarkdown=summary_md, model=ANTHROPIC_MODEL, cached=False, contentHash=c_hash)
    if allow_cache:
        SUMMARY_CACHE[cache_key] = SummaryResponse(**resp.dict(), cached=True)
    return resp
