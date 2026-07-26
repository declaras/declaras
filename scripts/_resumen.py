"""Imprime un resumen legible del estado de un job. Lee el JSON por stdin."""

import json
import sys

d = json.load(sys.stdin)
print(f"  estado: {d['status']}  (intentos: {d['attempts']})")
for doc in d.get("documents", []):
    print(f"    doc   {doc['doc_type']:<18} {doc['size_bytes']:>6}b  sha={doc['sha256'][:10]}")
for f in d.get("failures", []):
    print(f"    falla {f['doc_type']:<18} {f['code']} (reintentable={f['retryable']})")
if d.get("challenge"):
    c = d["challenge"]
    print(f"    reto  {c['kind']}: {c['prompt']}")
if d.get("error"):
    e = d["error"]
    print(f"    error {e['code']} (reintentable={e['retryable']}) {e.get('details', {})}")
