from __future__ import annotations
import os
import json
import pathlib
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from fastapi.responses import StreamingResponse
import hashlib
import requests
from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.errors import PyMongoError
import redis
import logging

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS_DIR = pathlib.Path(os.getenv("TESTS_DIR", ROOT / "tests"))

load_dotenv(ROOT / ".env")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
ANTHROPIC_MODEL = "claude-haiku-4-5-20251001"
MONGO_URL = "mongodb://localhost:27017"
MONGO_DB =  "momentic"
REDIS_URL = "redis://localhost:6379/0"

# Clients
tests_col = None
modules_col = None
redis_client = None
try:
    mongo_client = MongoClient(MONGO_URL)
    db = mongo_client[MONGO_DB]
    tests_col = db["tests"]
    modules_col = db["modules"]
    tests_col.create_index("id", unique=True, sparse=True)
    modules_col.create_index("moduleId", unique=True)
except PyMongoError as e:
    logging.error(f"Mongo connection/indexing failed: {e}")
    tests_col = None
    modules_col = None
try:
    redis_client = redis.Redis.from_url(REDIS_URL)
    redis_client.ping()
except Exception as e:
    logging.error(f"Redis connection failed: {e}")
    redis_client = None


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




def _step_synopsis(step: dict) -> str:
    t = (step.get("type") or "").upper()
   
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
    return ", ".join(bits)


def _summarize_steps(test: dict, modules_idx: dict) -> List[str]:
    res: List[str] = []
    steps = test.get("steps") or []
    for s in steps:
        stype = (s.get("type") or "").upper()
        if stype == "MODULE":
            mid = s.get("moduleId")
            m = modules_idx.get(mid)
            msteps: List[dict] = m.get("steps") or []
            for ms in msteps:
                res.append(_step_synopsis(ms))
        else:
            res.append(_step_synopsis(s))
    return res

 


def _build_summary_prompt(test: dict, synopsis: List[str]) -> tuple[str, str]:
    system = (
        "You are going to be given a test synopsis and you will need to summarize it."
        "Be concise and factual. Output sections: Purpose, Preconditions, Main Flow, Assertions."
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
        "max_tokens": 600,
        "system": system,
        "messages": [
            {"role": "user", "content": prompt},
        ],
        "stream": True,
    }
    try:
        with requests.post(url, headers=headers, data=json.dumps(body), stream=True, timeout=60) as r:
            if r.status_code >= 400:
                msg = (r.text or "Anthropic error").strip()
                yield f"event: error\ndata: {json.dumps(msg)}\n\n"
                return
            for raw in r.iter_lines(decode_unicode=True):
                if raw is None:
                    continue
                if raw == "":
                    # keep-alive from upstream
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
    except Exception as e:
        yield f"event: error\ndata: {json.dumps('Upstream error: ' + str(e))}\n\n"


def _anthropic_complete(system: str, prompt: str) -> str:
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
    try:
        r = requests.post(url, headers=headers, data=json.dumps(body), timeout=60)
        if r.status_code >= 400:
            return f"(Upstream error: {r.text[:500]})"
        data = r.json()
        content = data.get("content") or []
        text_blocks = [
            b.get("text")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        ]
        summary = "\n\n".join(text_blocks).strip()
        return summary or "(No summary returned)"
    except Exception as e:
        return f"(Request error: {str(e)[:200]})"




def _json_sha256(obj) -> str:
    try:
        s = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    except Exception:
        s = json.dumps(str(obj))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _content_hash_from_docs(test_raw: dict, modules_idx: dict) -> str:
    parts = [test_raw]
    for s in (test_raw.get("steps") or []):
        if (s.get("type") or "").upper() == "MODULE":
            module_id = s.get("moduleId")
            m = modules_idx.get(module_id)
            parts.append(m.get("raw"))
    return _json_sha256({"parts": parts})


def _get_cached_summary(test_id: str, c_hash: str):
    # Redis lookup only
    if redis_client is not None:
        try:
            val = redis_client.get(f"summary:{test_id}:{c_hash}")
            if val:
                print(f"CACHE HIT: Cached summary found for {test_id}: {c_hash}")
                summary = val.decode("utf-8") if isinstance(val, (bytes, bytearray)) else str(val)
                return summary
        except Exception:
            pass
    return None


def _store_cached_summary(test_id: str, c_hash: str, summary: str):
    # Redis write (with TTL)
    if redis_client is not None:
        try:
            redis_client.setex(f"summary:{test_id}:{c_hash}", 7 * 24 * 3600, summary)
        except Exception:
            pass


def _extract_module_ids(test_raw: dict) -> List[str]:
    ids: List[str] = []
    for s in (test_raw.get("steps") or []):
        stype = (s.get("type") or "").upper()
        if stype == "MODULE":
            mid = s.get("moduleId")
            if mid:
                ids.append(mid)
    return ids


def _load_modules_index_from_mongo_by_ids(module_ids: List[str]) -> dict:
    if not module_ids:
        return {}
    uniq = list({m for m in module_ids if m})
    mdocs = modules_col.find({"moduleId": {"$in": uniq}}, projection={"_id": 0})
    modules_idx: dict[str, dict] = {}
    for m in mdocs:
        mid = m.get("moduleId")
        if not mid:
            continue
        modules_idx[mid] = {
            "name": m.get("name"),
            "steps": m.get("steps") or [],
            "path": m.get("path"),
            "raw": m.get("raw"),
        }
    return modules_idx


def _prefetch_summaries_for_ids(ids: List[str]):
    if not ids:
        return
    if not ANTHROPIC_API_KEY:
        return
    # Pull raw docs for only the requested IDs
    docs = list(
        tests_col.find({"id": {"$in": ids}}, projection={"_id": 0, "id": 1, "raw": 1})
    )
    # Compute union of referenced moduleIds for this page
    mids_union: set[str] = set()
    for d in docs:
        raw = d.get("raw") or {}
        mids_union.update(_extract_module_ids(raw))
    modules_idx = _load_modules_index_from_mongo_by_ids(list(mids_union))
    for d in docs:
        tid = d.get("id")
        raw = d.get("raw") or {}
        synopsis = _summarize_steps(raw, modules_idx)
        system, prompt = _build_summary_prompt(raw, synopsis)
        c_hash = _content_hash_from_docs(raw, modules_idx)
        if _get_cached_summary(tid, c_hash):
            continue
        summary = _anthropic_complete(system, prompt)
        _store_cached_summary(tid, c_hash, summary)


@app.get("/health")
def health():
    redis_ok = False
    try:
        if redis_client is not None:
            redis_client.ping()
            redis_ok = True
    except Exception:
        redis_ok = False
    return {"ok": True, "testsDir": str(TESTS_DIR), "mongo": bool('tests_col' in globals() and tests_col is not None), "redis": redis_ok}


class PaginatedTests(BaseModel):
    items: List[TestSummary]
    total: int
    page: int
    pageSize: int
    totalPages: int


@app.get("/api/tests", response_model=PaginatedTests)
def get_tests(background_tasks: BackgroundTasks, page: int = Query(1, ge=1), pageSize: int = Query(20, ge=1, le=200)):
    if 'tests_col' not in globals() or tests_col is None:
        raise HTTPException(status_code=503, detail="Mongo not available")
    total = tests_col.count_documents({})
    skip = (page - 1) * pageSize
    cur = (
        tests_col
        .find({}, projection={"_id": 0, "raw": 0, "createdAtTs": 0, "updatedAtTs": 0})
        .sort("updatedAtTs", -1)
        .skip(skip)
        .limit(pageSize)
    )
    items: List[TestSummary] = []
    for d in cur:
        items.append(TestSummary(**d))
    total_pages = (total + pageSize - 1) // pageSize
    if background_tasks:
        ids = [it.id for it in items if it.id]
        background_tasks.add_task(_prefetch_summaries_for_ids, ids)
    return PaginatedTests(items=items, total=total, page=page, pageSize=pageSize, totalPages=total_pages)


@app.get("/api/tests/{test_id}/summary/stream")
def stream_test_summary(test_id: str):
    if tests_col is None or modules_col is None:
        raise HTTPException(status_code=503, detail="Mongo not available")
    test_doc = tests_col.find_one({"id": test_id})
    if not test_doc:
        raise HTTPException(status_code=404, detail="Test not found")
    test_doc = test_doc.get("raw") or {}
    # Load only referenced modules
    mids = _extract_module_ids(test_doc)
    modules_idx = _load_modules_index_from_mongo_by_ids(mids)
    synopsis = _summarize_steps(test_doc, modules_idx)
    system, prompt = _build_summary_prompt(test_doc, synopsis)
    c_hash = _content_hash_from_docs(test_doc, modules_idx)
    cached = _get_cached_summary(test_id, c_hash)

    def event_stream():
        yield "retry: 300\n\n"
        yield ": keep-alive\n\n"
        if cached:
            yield f"data: {json.dumps(cached)}\n\n"
            yield "event: done\ndata: done\n\n"
            return
        buffer: list[str] = []
        for chunk in _anthropic_stream(system, prompt):
            if chunk.startswith("data: "):
                try:
                    delta = json.loads(chunk[6:])
                    if isinstance(delta, str):
                        buffer.append(delta)
                except Exception:
                    pass
            yield chunk
        if buffer:
            full = "".join(buffer)
            _store_cached_summary(test_id, c_hash, full)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@app.on_event("shutdown")
def _flush_redis_on_shutdown():
    # Ensure a fresh cache on next start
    if redis_client is not None:
        try:
            redis_client.flushdb()
            logging.info("Flushed Redis DB on shutdown")
        except Exception as e:
            logging.error(f"Failed to flush Redis on shutdown: {e}")
