#!/usr/bin/env python3
"""Bounded equivalence check for two geometric-v5 artifact directories.

Rows are joined through a temporary on-disk SQLite index by source_index; chunk
boundaries are deliberately ignored.  The process keeps only one NPZ chunk in
memory at once.  SQLite payload storage trades disk for bounded RAM and is
intentionally explicit rather than silently sampling a large artifact.
"""
from __future__ import annotations

import argparse, hashlib, heapq, json, pickle, struct, sys, tempfile
from pathlib import Path
from typing import Any

import numpy as np

ATOL = 1e-6
RTOL = 1e-5
CONFIDENCE_ATOL = 1e-6


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _artifact(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest = _load_json(root / "manifest.json")
    if manifest.get("analysisSchema") != "rebar-analysis-v2" or manifest.get("algorithm", {}).get("id") != "geometric-v5":
        raise ValueError(f"{root}: not a V5 analysis artifact")
    features = _load_json(root / manifest.get("featuresPath", "features/manifest.json"))
    labels = _load_json(root / manifest.get("labelsPath", "labels/manifest.json"))
    if features.get("schema") != "rebar-features-v1" or labels.get("schema") != "rebar-raw-labels-v2":
        raise ValueError(f"{root}: unsupported V5 sidecar schema")
    return manifest, features, labels


def _check_chunks(root: Path, folder: str, manifest: dict[str, Any], report: dict[str, Any]) -> None:
    for chunk in manifest.get("chunks", []):
        path = root / folder / chunk["path"]
        actual = _sha(path)
        report["chunkChecksums"].append({"artifact": str(root), "kind": folder, "path": chunk["path"], "ok": actual == chunk.get("sha256")})
        if actual != chunk.get("sha256"):
            raise ValueError(f"{root}/{folder}/{chunk['path']}: checksum mismatch")


def _candidate_map(payload: Any, count: int) -> list[tuple[int, ...]]:
    result = [tuple() for _ in range(count)]
    names = {"candidate_point_indices", "candidate_offsets", "candidate_instance_ids"}
    if not names & set(payload.files):
        return result
    if not names <= set(payload.files):
        raise ValueError("incomplete candidate CSR")
    rows, offsets, identifiers = (np.asarray(payload[name]) for name in ("candidate_point_indices", "candidate_offsets", "candidate_instance_ids"))
    if len(offsets) != len(rows) + 1 or offsets[0] != 0 or offsets[-1] != len(identifiers):
        raise ValueError("invalid candidate CSR")
    for row, start, end in zip(rows, offsets[:-1], offsets[1:]):
        if row < 0 or row >= count:
            raise ValueError("candidate CSR row out of bounds")
        result[int(row)] = tuple(sorted(int(value) for value in identifiers[int(start):int(end)]))
    return result


def _rows(path: Path, kind: str, stage: str | None = None):
    with np.load(path, allow_pickle=False) as payload:
        prefix = f"{stage}_" if stage else ""
        index_key = prefix + "source_index"
        if stage and index_key not in payload.files:
            return
        if index_key not in payload.files:
            raise ValueError(f"{path}: missing source_index")
        indices = np.asarray(payload[index_key], dtype=np.uint64)
        candidates = _candidate_map(payload, len(indices)) if kind == "labels" else [tuple()] * len(indices)
        fields = [name for name in payload.files if name.startswith(prefix) and name not in {index_key, "candidate_point_indices", "candidate_offsets", "candidate_instance_ids"}]
        arrays = {name: payload[name] for name in fields}
        for name in fields:
            if len(arrays[name]) != len(indices):
                raise ValueError(f"{path}: {name} length differs from source_index")
        for row, source in enumerate(indices):
            values = {name.removeprefix(prefix): np.asarray(arrays[name][row]) for name in fields}
            if kind == "labels":
                values["__candidates__"] = candidates[row]
            yield int(source), values


def _encode(values: dict[str, Any]) -> bytes:
    return pickle.dumps(values, protocol=5)


def _equal_value(name: str, first: Any, second: Any) -> bool:
    left, right = np.asarray(first), np.asarray(second)
    if left.shape != right.shape:
        return False
    if name in {"class_confidence", "instance_confidence"}:
        return bool(np.allclose(left, right, rtol=0, atol=CONFIDENCE_ATOL, equal_nan=True))
    if left.dtype.kind in "fc" or right.dtype.kind in "fc":
        return bool(np.allclose(left, right, rtol=RTOL, atol=ATOL, equal_nan=True))
    return bool(np.array_equal(left, right))


def _compare_values(first: dict[str, Any], second: dict[str, Any]) -> str | None:
    if set(first) != set(second):
        return f"attribute keys differ: {sorted(set(first) ^ set(second))}"
    for name in sorted(first):
        if name == "__candidates__":
            if first[name] != second[name]:
                return "candidate CSR membership differs"
        elif not _equal_value(name, first[name], second[name]):
            return f"attribute differs: {name}"
    return None


def _write_runs(root: Path, folder: str, manifest: dict[str, Any], report: dict[str, Any], temporary: Path, prefix: str, stage: str | None = None) -> list[Path]:
    """External-sort each input chunk without allocating by maximum source_index.

    Memory is bounded by one NPZ chunk. Each emitted record is uint64 source index
    plus a length-prefixed pickle payload, so non-contiguous indices and arbitrary
    reader chunk layouts remain valid.
    """
    _check_chunks(root, folder, manifest, report)
    runs: list[Path] = []
    for ordinal, chunk in enumerate(manifest["chunks"]):
        records = list(_rows(root / folder / chunk["path"], folder, stage))
        records.sort(key=lambda item: item[0])
        run = temporary / f"{prefix}-{folder}-{ordinal:06d}.run"
        with run.open("wb") as handle:
            previous = None
            for source, values in records:
                if source == previous:
                    raise ValueError(f"{root}/{folder}: duplicate source_index {source}")
                previous = source
                payload = _encode(values)
                handle.write(struct.pack("<QI", source, len(payload)))
                handle.write(payload)
        runs.append(run)
    return runs


def _read_record(handle):
    header = handle.read(12)
    if not header:
        return None
    if len(header) != 12:
        raise ValueError("truncated external comparison run")
    source, size = struct.unpack("<QI", header)
    payload = handle.read(size)
    if len(payload) != size:
        raise ValueError("truncated external comparison payload")
    return source, pickle.loads(payload)


def _merged_runs(runs: list[Path]):
    handles = [path.open("rb") for path in runs]
    heap = []
    try:
        for ordinal, handle in enumerate(handles):
            record = _read_record(handle)
            if record is not None:
                heapq.heappush(heap, (record[0], ordinal, record[1]))
        previous = None
        while heap:
            source, ordinal, values = heapq.heappop(heap)
            if source == previous:
                raise ValueError(f"duplicate source_index across external runs: {source}")
            previous = source
            following = _read_record(handles[ordinal])
            if following is not None:
                heapq.heappush(heap, (following[0], ordinal, following[1]))
            yield source, values
    finally:
        for handle in handles:
            handle.close()


def _compare_streams(first, second, folder: str, report: dict[str, Any]) -> None:
    first_item, second_item = next(first, None), next(second, None)
    unmatched_first = unmatched_second = 0
    while first_item is not None or second_item is not None:
        if second_item is None or (first_item is not None and first_item[0] < second_item[0]):
            unmatched_first += 1
            if unmatched_first <= 20:
                report["mismatches"].append({"kind": folder, "sourceIndex": first_item[0], "reason": "missing from second artifact"})
            first_item = next(first, None)
        elif first_item is None or second_item[0] < first_item[0]:
            unmatched_second += 1
            if unmatched_second <= 20:
                report["mismatches"].append({"kind": folder, "sourceIndex": second_item[0], "reason": "missing from first artifact"})
            second_item = next(second, None)
        else:
            reason = _compare_values(first_item[1], second_item[1])
            if reason and sum(item.get("kind") == folder for item in report["mismatches"]) < 20:
                report["mismatches"].append({"kind": folder, "sourceIndex": first_item[0], "reason": reason})
            first_item, second_item = next(first, None), next(second, None)
    report["counts"][folder] = {"unmatchedFirst": unmatched_first, "unmatchedSecond": unmatched_second}

def _strip_runtime(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _strip_runtime(item) for key, item in value.items() if key not in {"diagnostics", "elapsedS", "temporaryByteSize", "updatedAt"} and not key.lower().endswith("elapsed")}
    if isinstance(value, list):
        values = [_strip_runtime(item) for item in value]
        if all(isinstance(item, dict) and "id" in item for item in values):
            return sorted(values, key=lambda item: item["id"])
        return values
    return value


def _json_equal(first: Any, second: Any) -> bool:
    if isinstance(first, (int, bool)) or isinstance(second, (int, bool)):
        return type(first) is type(second) and first == second
    if isinstance(first, (int, float)) and isinstance(second, (int, float)):
        return bool(np.isclose(first, second, rtol=RTOL, atol=ATOL, equal_nan=True))
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return set(first) == set(second) and all(_json_equal(first[key], second[key]) for key in first)
    if isinstance(first, list):
        return len(first) == len(second) and all(_json_equal(a, b) for a, b in zip(first, second))
    return first == second


def compare(first_root: Path, second_root: Path) -> dict[str, Any]:
    report: dict[str, Any] = {"schema": "rebar-v5-compare-v1", "first": str(first_root), "second": str(second_root), "atol": ATOL, "rtol": RTOL, "confidenceAtol": CONFIDENCE_ATOL, "chunkChecksums": [], "mismatches": [], "counts": {}}
    first_manifest, first_features, first_labels = _artifact(first_root)
    second_manifest, second_features, second_labels = _artifact(second_root)
    if first_features.get("attributes") != second_features.get("attributes") or first_labels.get("attributes") != second_labels.get("attributes"):
        report["mismatches"].append({"kind": "manifest", "reason": "sidecar attribute schemas differ"})
    with tempfile.TemporaryDirectory(prefix="rebar-v5-compare-") as temporary_name:
        temporary = Path(temporary_name)
        for folder, first_sidecar, second_sidecar in (("features", first_features, second_features), ("labels", first_labels, second_labels)):
            first_runs = _write_runs(first_root, folder, first_sidecar, report, temporary, "first")
            second_runs = _write_runs(second_root, folder, second_sidecar, report, temporary, "second")
            _compare_streams(_merged_runs(first_runs), _merged_runs(second_runs), folder, report)
        first_updates = first_features.get('boundaryUpdates', {'chunks': []})
        second_updates = second_features.get('boundaryUpdates', {'chunks': []})
        for key in ('encoding', 'applyOrder', 'attributes'):
            if first_updates.get(key) != second_updates.get(key):
                report['mismatches'].append({'kind': 'boundaryUpdates', 'reason': f'{key} differs'})
        for stage in ('table', 'fixture'):
            first_runs = _write_runs(first_root, 'features', first_updates, report, temporary, f'first-{stage}', stage)
            second_runs = _write_runs(second_root, 'features', second_updates, report, temporary, f'second-{stage}', stage)
            _compare_streams(_merged_runs(first_runs), _merged_runs(second_runs), f'boundaryUpdates.{stage}', report)
    first_result = _strip_runtime(_load_json(first_root / first_manifest.get("resultPath", "result.json")).get("analysis", {}))
    second_result = _strip_runtime(_load_json(second_root / second_manifest.get("resultPath", "result.json")).get("analysis", {}))
    if not _json_equal(first_result, second_result):
        report["mismatches"].append({"kind": "geometry", "reason": "analysis geometry differs after runtime diagnostics are removed"})
    report["equal"] = not report["mismatches"]
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first_artifact", type=Path)
    parser.add_argument("second_artifact", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = compare(args.first_artifact, args.second_artifact)
        status = 0 if report["equal"] else 1
    except Exception as error:
        report = {"schema": "rebar-v5-compare-v1", "equal": False, "error": {"type": type(error).__name__, "message": str(error)}}
        status = 2
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    raise SystemExit(status)


if __name__ == "__main__":
    main()
