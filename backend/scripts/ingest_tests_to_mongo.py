#!/usr/bin/env python3
from __future__ import annotations
import os
import pathlib
import yaml
from datetime import datetime
from pymongo import MongoClient, ReplaceOne

ROOT = pathlib.Path(__file__).resolve().parents[2]
TESTS_DIR = pathlib.Path(os.getenv("TESTS_DIR", ROOT / "tests"))
MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "momentic")


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).astimezone().isoformat()


def _read_yaml(p: pathlib.Path) -> dict:
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def main():
    if not TESTS_DIR.exists():
        raise SystemExit(f"Tests directory not found: {TESTS_DIR}")

    client = MongoClient(MONGO_URL)
    db = client[MONGO_DB]
    tests_col = db["tests"]
    modules_col = db["modules"]

    tests_col.create_index("id", unique=True, sparse=True)
    tests_col.create_index("filePath")
    modules_col.create_index("moduleId", unique=True)

    test_ops = []
    module_ops = []

    for p in sorted(TESTS_DIR.rglob("*.test.yaml")):
        data = _read_yaml(p)
        file_type = (data.get("fileType") or "").lower()
        if "test" not in file_type:
            continue
        steps = data.get("steps")
        stat = p.stat()
        rel = str(p.relative_to(ROOT))
        labels = data.get("labels") or []
        disabled = bool(data.get("disabled") or False)
        doc = {
            "id": data.get("id") or rel,  # fallback to path
            "name": data.get("name") or p.stem,
            "description": data.get("description") or "",
            "filePath": rel,
            "createdAt": _iso(stat.st_ctime),
            "updatedAt": _iso(stat.st_mtime),
            "createdAtTs": stat.st_ctime,
            "updatedAtTs": stat.st_mtime,
            "stepCount": len(steps) if isinstance(steps, list) else 0,
            "labels": labels,
            "disabled": disabled,
            "raw": data,  # store full YAML doc
        }
        test_ops.append(
            ReplaceOne({"id": doc["id"]}, doc, upsert=True)
        )

    for p in sorted(TESTS_DIR.rglob("*.module.yaml")):
        data = _read_yaml(p)
        module_id = data.get("moduleId")
        if not module_id:
            continue
        obj = {
            "moduleId": module_id,
            "name": data.get("name") or p.stem,
            "steps": data.get("steps") or [],
            "path": str(p.relative_to(ROOT)),
            "raw": data,
        }
        module_ops.append(
            ReplaceOne({"moduleId": module_id}, obj, upsert=True)
        )

    if test_ops:
        res = tests_col.bulk_write(test_ops, ordered=False)
        print(f"Tests upserted: {res.upserted_count}, modified: {res.modified_count}")
    else:
        print("No tests found to ingest.")

    if module_ops:
        resm = modules_col.bulk_write(module_ops, ordered=False)
        print(f"Modules upserted: {resm.upserted_count}, modified: {resm.modified_count}")
    else:
        print("No modules found to ingest.")

    client.close()


if __name__ == "__main__":
    main()
