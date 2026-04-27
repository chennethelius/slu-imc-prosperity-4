#!/usr/bin/env python3
"""Fetch the latest IMC algo submission's zip artifact.

Uses the stored prosperity-cli config (email + refresh_token). Downloads the
submission's .py / .json / .log into backtests/imc-<id>/ for inspection.
"""
import io
import sys
import zipfile
from pathlib import Path

import requests
from prosperity_cli.config import load as load_config
from prosperity_cli.submit import _api, _get_token, _current_round


def main():
    cfg = load_config()
    token = _get_token(cfg)
    round_id = _current_round(token)
    print(f"round_id={round_id}", flush=True)

    body = _api("GET", f"/submissions/algo/{round_id}?page=1&pageSize=20", token).json()
    if isinstance(body, list):
        items = body
    elif isinstance(body, dict):
        # Walk common envelope shapes: data.submissions, data.items, data
        d = body.get("data", body)
        if isinstance(d, list):
            items = d
        elif isinstance(d, dict):
            items = d.get("submissions") or d.get("items") or d.get("results") or []
        else:
            items = []
    else:
        items = []
    if not items:
        import json as _j
        print("Response shape unexpected — raw body:")
        print(_j.dumps(body, indent=2)[:2000])
        return
    print(f"submissions on round {round_id}: {len(items)}")
    if items:
        import json as _j
        print(f"first item keys: {list(items[0].keys())}")
        print(_j.dumps(items[0], indent=2)[:1500])
        print("---")
    for s in items[:15]:
        sid = s.get('id','?')
        # Try common name fields
        nm = s.get('algorithmName') or s.get('name') or s.get('fileName') or s.get('filename') or '?'
        print(f"  id={str(sid)}  status={s.get('status','?'):<12}  name={nm}  "
              f"pnl={s.get('pnl', s.get('totalPnl', s.get('finalPnl', '?')))}  "
              f"created={s.get('createdAt') or s.get('submittedAt') or s.get('timestamp','?')}")

    finished = [s for s in items if s.get("status") == "FINISHED"]
    if not finished:
        print("No finished submissions yet")
        return

    target_name = sys.argv[1] if len(sys.argv) > 1 else "v62"
    matches = [s for s in finished if target_name in (s.get("filename") or "")]
    if not matches:
        print(f"No FINISHED submission matching '{target_name}'. Picking latest FINISHED.")
        matches = finished
    target = matches[0]
    sub_id = target["id"]
    fname = target.get("filename", f"algo-{sub_id}")
    print(f"\nDownloading sub_id={sub_id} filename={fname}")
    print(f"  submittedAt: {target.get('submittedAt')}")

    zip_body = _api("GET", f"/submissions/algo/{sub_id}/zip", token).json()
    # Walk dict envelope to find a string URL
    def _find_url(o):
        if isinstance(o, str) and o.startswith("http"):
            return o
        if isinstance(o, dict):
            for k in ("url", "signedUrl", "downloadUrl", "data"):
                if k in o:
                    u = _find_url(o[k])
                    if u:
                        return u
            for v in o.values():
                u = _find_url(v)
                if u:
                    return u
        return None
    zip_url = _find_url(zip_body)
    if not zip_url:
        import json as _j
        print("Could not find zip URL in response. Body:")
        print(_j.dumps(zip_body, indent=2)[:1500])
        return
    print(f"  zip URL: {zip_url[:80]}...")
    zip_bytes = requests.get(zip_url, timeout=60).content

    out_dir = Path("backtests") / f"imc-{sub_id}-{Path(fname).stem}"
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.extractall(out_dir)
            print(f"Extracted {len(zf.namelist())} files to {out_dir}")
            for name in zf.namelist():
                p = out_dir / name
                print(f"  {p}  ({p.stat().st_size} bytes)")
    except zipfile.BadZipFile:
        (out_dir / "raw").write_bytes(zip_bytes)
        print(f"Not a zip; wrote raw bytes to {out_dir}/raw")


if __name__ == "__main__":
    main()
