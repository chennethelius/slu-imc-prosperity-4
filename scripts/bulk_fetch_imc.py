#!/usr/bin/env python3
"""Fetch and parse PnL from multiple IMC submissions."""
import io
import json
import sys
import zipfile
from pathlib import Path

import requests
from prosperity_cli.config import load as load_config
from prosperity_cli.submit import _api, _get_token, _current_round


def parse_pnl(zip_bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            log_name = next(n for n in zf.namelist() if n.endswith(".log"))
            d = json.loads(zf.read(log_name).decode("utf-8"))
    except Exception:
        return None
    lines = d["activitiesLog"].strip().split("\n")
    header = lines[0].split(";")
    pnl_idx = header.index("profit_and_loss")
    prod_idx = header.index("product")
    last = {}
    for line in lines[1:]:
        p = line.split(";")
        if len(p) <= pnl_idx:
            continue
        try:
            last[p[prod_idx]] = float(p[pnl_idx])
        except ValueError:
            pass
    return sum(last.values()), last


def main():
    cfg = load_config()
    token = _get_token(cfg)
    round_id = _current_round(token)
    body = _api("GET", f"/submissions/algo/{round_id}?page=1&pageSize=30", token).json()
    if isinstance(body, list):
        items = body
    else:
        d = body.get("data", body)
        items = d.get("submissions") or d.get("items") or d.get("results") or (d if isinstance(d, list) else [])
    print(f"{len(items)} submissions on round {round_id}")
    print(f"{'id':>8}  {'fname':<35} {'total':>10}")
    for s in items[:int(sys.argv[1]) if len(sys.argv) > 1 else 10]:
        sid = s["id"]
        fname = s.get("filename", "?")
        if s.get("status") != "FINISHED":
            print(f"  {sid:>8}  {fname:<35} (status={s.get('status')})")
            continue
        zip_body = _api("GET", f"/submissions/algo/{sid}/zip", token).json()

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
            continue
        try:
            zb = requests.get(zip_url, timeout=60).content
        except Exception as e:
            print(f"  {sid}: download failed {e}")
            continue
        result = parse_pnl(zb)
        if result is None:
            print(f"  {sid}  {fname:<35} (parse failed)")
            continue
        total, _ = result
        print(f"  {sid:>8}  {fname:<35} {total:>10,.2f}")


if __name__ == "__main__":
    main()
