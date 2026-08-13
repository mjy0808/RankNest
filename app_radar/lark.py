from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


SEGMENT_TITLES = {
    "app": "App",
    "mobile_game": "手游",
    "steam_game": "Steam 游戏",
}
ALLOWED_WEBHOOK_HOSTS = {"open.feishu.cn", "open.larksuite.com"}


def _top_lines(payload: dict[str, Any], count: int = 3) -> list[str]:
    lines: list[str] = []
    sections = payload.get("sections", {})
    for segment in ("app", "mobile_game", "steam_game"):
        items = sections.get(segment, []) if isinstance(sections, dict) else []
        lines.append(f"**{SEGMENT_TITLES[segment]} Top {min(count, len(items))}**")
        for item in items[:count]:
            reasons = item.get("reasons") or []
            reason = f" · {reasons[0]}" if reasons else ""
            lines.append(
                f"{item.get('rank', '-')}. {item.get('name', '未知')} "
                f"— {float(item.get('score', 0)):.1f} 分{reason}"
            )
        lines.append("")
    return lines


def build_card(payload: dict[str, Any], report_url: str) -> dict[str, Any]:
    generated_at = str(payload.get("generated_at", ""))[:10]
    healthy = sum(
        status.get("state") == "healthy" for status in payload.get("source_status", [])
    )
    content = "\n".join(_top_lines(payload)).strip()
    content += (
        f"\n\n---\n从 **{int(payload.get('total_candidates', 0)):,}** 个候选中筛选"
        f" · **{healthy}** 个健康数据源 · run #{payload.get('run_id', '-')}"
    )
    return {
        "msg_type": "interactive",
        "card": {
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": {
                "title": {
                    "tag": "plain_text",
                    "content": f"全球潜力 App / 游戏日报 · {generated_at}",
                },
                "template": "turquoise",
            },
            "body": {
                "direction": "vertical",
                "padding": "12px 12px 12px 12px",
                "elements": [
                    {
                        "tag": "markdown",
                        "content": content,
                        "text_align": "left",
                        "text_size": "normal_v2",
                        "margin": "0px 0px 12px 0px",
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "查看完整 Top 20 报告"},
                        "type": "primary",
                        "width": "fill",
                        "size": "medium",
                        "behaviors": [{"type": "open_url", "default_url": report_url}],
                    },
                ],
            },
        },
    }


def send_card(webhook_url: str, card: dict[str, Any]) -> None:
    parsed = urlparse(webhook_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_WEBHOOK_HOSTS
        or not parsed.path.startswith("/open-apis/bot/v2/hook/")
    ):
        raise ValueError("LARK_WEBHOOK_URL 必须使用飞书或 Lark 官方 HTTPS 域名")
    body = json.dumps(card, ensure_ascii=False).encode("utf-8")
    if len(body) > 20_000:
        raise ValueError("飞书卡片超过 20 KB 限制")
    request = Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError) as exc:
        raise RuntimeError(f"飞书 Webhook 请求失败：{exc}") from exc
    if "code" in result:
        code = result["code"]
    elif "StatusCode" in result:
        code = result["StatusCode"]
    else:
        raise RuntimeError("飞书 Webhook 返回了无法识别的响应")
    if code != 0:
        message = result.get("msg", result.get("StatusMessage", "未知错误"))
        raise RuntimeError(f"飞书 Webhook 返回失败：{code} {message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发送 RankNest 飞书摘要卡片")
    parser.add_argument("--report", type=Path, default=Path("reports/latest.json"))
    parser.add_argument("--url", required=True, help="GitHub Pages 完整报告地址")
    args = parser.parse_args(argv)
    webhook_url = os.getenv("LARK_WEBHOOK_URL", "").strip()
    if not webhook_url:
        parser.error("缺少 LARK_WEBHOOK_URL")
    payload = json.loads(args.report.read_text(encoding="utf-8"))
    send_card(webhook_url, build_card(payload, args.url))
    print("飞书摘要卡片发送成功")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
