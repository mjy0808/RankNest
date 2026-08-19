from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .pipeline import run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app-radar",
        description="生成全球高增长、可借鉴 App / 手游机会日报",
    )
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--db", type=Path, default=Path("data/radar.db"))
    parser.add_argument("--output", type=Path, default=Path("reports"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        result = run_pipeline(config, args.db, args.output)
        print(
            f"完成：从 {result.total_candidates} 个候选生成 Top {result.selected_count}，"
            f"run #{result.run_id}，报告位于 {result.artifacts.latest_html}"
        )
        issues = [status for status in result.statuses if status.state in {"degraded", "failed"}]
        if issues:
            print(f"提醒：{len(issues)} 个数据源降级或失败，详见报告", file=sys.stderr)
        return 0
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return 1
