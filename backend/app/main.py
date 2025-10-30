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
from fastapi.responses import StreamingResponse
import requests
from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS_DIR = pathlib.Path(os.getenv("TESTS_DIR", ROOT / "tests"))

load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"

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


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat()



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
        createdAt=_iso(stat.st_ctime),
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


def _summarize_steps(test: dict, modules_idx: dict) -> List[str]:
    res: List[str] = []
    steps = test.get("steps") or []
    for s in steps:
        stype = (s.get("type") or "").upper()
        if stype == "MODULE":
            mid = s.get("moduleId")
            m = modules_idx.get(mid)
            if not m:
                res.append(f"MISSING MODULE: {mid}")
                continue
            msteps: List[dict] = m.get("steps") or []
            for ms in msteps:
                res.append(_step_synopsis(ms))
        else:
            res.append(_step_synopsis(s))
    return res

 


def _build_summary_prompt(test: dict, synopsis: List[str]) -> tuple[str, str]:
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
    return system, prompt


def _anthropic_stream(system: str, prompt: str):
    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
        "accept": "text/event-stream",
    }
    body = {
        "model": ANTHROPIC_MODEL,
        "system": system,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "stream": True,
    }
    with requests.post(url, headers=headers, data=json.dumps(body), stream=True, timeout=60) as r:
        if r.status_code >= 400:
            msg = (r.text or "Anthropic error").strip()
            yield f"event: error\ndata: {json.dumps(msg)}\n\n"
            return
        for raw in r.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if raw.startswith("data: "):
                try:
                    payload = json.loads(raw[6:])
                except Exception:
                    continue
                etype = payload.get("type") or payload.get("event")
                if etype == "content_block_delta":
                    delta = payload.get("delta") or {}
                    if isinstance(delta, dict) and delta.get("type") == "text_delta":
                        text = delta.get("text") or ""
                        if text:
                            yield f"data: {json.dumps(text)}\n\n"
                elif etype == "message_stop":
                    yield "event: done\ndata: done\n\n"
                    return
                elif etype == "error":
                    err = payload.get("error") or payload
                    yield f"event: error\ndata: {json.dumps(str(err))}\n\n"
                    return
        yield "event: done\ndata: done\n\n"


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


@app.get("/api/tests/{test_id}/summary/stream")
def stream_test_summary(test_id: str):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=400, detail="Missing ANTHROPIC_API_KEY in environment")

    test_path = _find_test_file_by_id(test_id)
    if not test_path:
        raise HTTPException(status_code=404, detail="Test not found")

    test_doc = _read_yaml(test_path)
    modules_idx = _build_module_index()
    synopsis = _summarize_steps(test_doc, modules_idx)
    system, prompt = _build_summary_prompt(test_doc, synopsis)

    def event_stream():
        # Optional SSE retry suggestion
        yield "retry: 300\n\n"
        for chunk in _anthropic_stream(system, prompt):
            yield chunk

    return StreamingResponse(event_stream(), media_type="text/event-stream")
