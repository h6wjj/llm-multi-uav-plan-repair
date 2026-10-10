"""Read-only packaging checks; never launches a scientific experiment."""
import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = json.loads((ROOT / "MANIFEST.json").read_text(encoding="utf-8-sig"))
    failures, warnings = [], []
    counts = {"files": 0, "python": 0, "json": 0, "jsonl_records": 0}
    literal_secret = re.compile(r"(?:sk-[A-Za-z0-9_-]{24,}|ghp_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
    credential_assignment = re.compile(r'''(?i)["']?(?:api_key|access_token|secret_key|password)["']?\s*[:=]\s*["']([^"'\n]{8,})["']''')
    for item in manifest["files"]:
        path = (ROOT / item["path"]).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            failures.append({"path": item["path"], "check": "missing_or_outside"})
            continue
        raw = path.read_bytes()
        counts["files"] += 1
        if len(raw) != item["bytes"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
            failures.append({"path": item["path"], "check": "packaged_bytes"})
        if path.suffix not in {".py", ".json", ".jsonl", ".md", ".txt", ".csv", ".toml", ".yaml", ".yml"}:
            continue
        try:
            content = raw.decode("utf-8-sig")
            if path.suffix == ".py":
                ast.parse(content, filename=item["path"])
                counts["python"] += 1
            if path.suffix == ".json":
                json.loads(content)
                counts["json"] += 1
            if path.suffix == ".jsonl":
                for number, line in enumerate(content.splitlines(), 1):
                    if line.strip():
                        json.loads(line)
                        counts["jsonl_records"] += 1
            # Do not emit matched values. Text may contain benign sample credentials.
            if path.name != "check_package.py" and literal_secret.search(content):
                warnings.append({"path": item["path"], "check": "potential_literal_secret"})
            if path.name != "check_package.py" and credential_assignment.search(content):
                warnings.append({"path": item["path"], "check": "credential_literal_needs_review"})
        except (UnicodeError, ValueError, SyntaxError) as exc:
            failures.append({"path": item["path"], "check": "text_format", "error_type": type(exc).__name__})
    index = json.loads((ROOT / "paper_applied_intelligence/REPRODUCTION_INDEX.json").read_text(encoding="utf-8"))
    for group in index["groups"]:
        for relative in group["inputs"] + group["row_files"] + [group["summary"], group["analysis_entry"]]:
            if not (ROOT / relative).is_file():
                failures.append({"group": group["id"], "path": relative, "check": "index_target"})
    print(json.dumps({"scope": "local_package_integrity_not_result_reproduction", "counts": counts,
                      "groups": len(index["groups"]), "failures": failures, "warnings": warnings,
                      "experiments_run": False, "api_called": False}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
