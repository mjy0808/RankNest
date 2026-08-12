from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .mailer import send_report
from .pipeline import mark_run_sent, run_pipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app-radar",
        description="生成全球潜力 App / 游戏 Top 20 日报",
    )
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument("--db", type=Path, default=Path("data/radar.db"))
    parser.add_argument("--output", type=Path, default=Path("reports"))
    parser.add_argument("--send", action="store_true", help="生成后尝试通过 SMTP 发送")
    parser.add_argument(
        "--require-email", action="store_true", help="邮件配置缺失时返回失败，适合自动任务"
    )
    parser.add_argument(
        "--force-send", action="store_true", help="即使当天已经发送也再次发送"
    )
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
        if args.send:
            if result.already_sent and not args.force_send:
                print("当天报告已经发送，已跳过重复邮件")
                return 0
            subject = f"{config.report.title} · {result.artifacts.dated_html.stem}"
            sent, message = send_report(
                subject,
                result.artifacts.html_body,
                result.artifacts.text_body,
            )
            print(message)
            if sent:
                mark_run_sent(args.db, result.run_id)
            elif args.require_email:
                return 2
        elif args.require_email:
            print("--require-email 必须和 --send 一起使用", file=sys.stderr)
            return 2
        return 0
    except Exception as exc:
        print(f"运行失败：{exc}", file=sys.stderr)
        return 1
