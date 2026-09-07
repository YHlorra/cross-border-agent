"""CLI: python -m agent.listing export|channel — 标准文件出口（tier-2 通道）。

export: 填参即出规范文件——固定字段从市场 profile 预填，可变项用 CLI 参数
覆盖默认（价格缺省从落库候选带出），固定字段永不重复输入。
channel: 打印当前上传通道（api = 凭据齐全 / export = 退化导出）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m agent.listing")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_exp = sub.add_parser("export", help="渲染标准文件（API payload + 逐字段复制块）")
    p_exp.add_argument("--run-id", required=True, help="listing_runs.id")
    p_exp.add_argument("--price", type=float, default=None, help="售价 USD（缺省=候选带出）")
    p_exp.add_argument("--quantity", type=int, default=None)
    p_exp.add_argument("--sku", default=None)
    p_exp.add_argument("--brand", default=None, help="卖家品牌（缺省=空，走无品牌豁免）")
    p_exp.add_argument("--out", default=None, help="写文件；缺省打印 stdout")

    sub.add_parser("channel", help="显示当前上传通道")

    args = parser.parse_args(argv)

    if args.cmd == "channel":
        from .channel import resolve_upload_channel
        from .spapi_store import load_spapi_creds

        print(resolve_upload_channel(load_spapi_creds()))
        return 0

    from ..persistence import get_listing_run
    from .export import render_export

    run = get_listing_run(args.run_id)
    if run is None:
        print(f"listing run not found: {args.run_id}", file=sys.stderr)
        return 1

    overrides: dict = {}
    if args.price is not None:
        overrides["price_usd"] = args.price
    if args.quantity is not None:
        overrides["quantity"] = args.quantity
    if args.sku is not None:
        overrides["sku"] = args.sku
    if args.brand is not None:
        overrides["brand"] = args.brand

    export = render_export(run, facts_overrides=overrides)
    text = json.dumps(export, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"exported -> {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
