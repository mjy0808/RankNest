from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import ScoredCandidate, SourceStatus
from .scoring import SEGMENT_LABELS


@dataclass(frozen=True)
class ReportArtifacts:
    dated_html: Path
    latest_html: Path
    latest_text: Path
    latest_json: Path
    html_body: str
    text_body: str


def _compact_number(value: int) -> str:
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f}万"
    return f"{value:,}"


def _source_label(source: str) -> str:
    return {"apple": "App Store", "steam": "Steam"}.get(source, source.title())


def _item_dict(rank: int, item: ScoredCandidate) -> dict[str, Any]:
    candidate = item.candidate
    return {
        "rank": rank,
        "segment": candidate.segment,
        "segment_label": SEGMENT_LABELS[candidate.segment],
        "score": item.score,
        "source": candidate.source,
        "external_id": candidate.external_id,
        "name": candidate.name,
        "kind": candidate.kind,
        "developer": candidate.developer,
        "url": candidate.url,
        "image_url": candidate.image_url,
        "release_date": candidate.release_date.isoformat() if candidate.release_date else None,
        "update_date": candidate.update_date.isoformat() if candidate.update_date else None,
        "rating": round(candidate.rating, 2),
        "review_count": candidate.review_count,
        "market_count": candidate.market_count,
        "best_rank": candidate.best_rank,
        "average_rank": round(candidate.average_rank, 2),
        "social_mentions": candidate.social_mentions,
        "components": item.components,
        "component_weights": item.component_weights,
        "reasons": list(item.reasons),
        "markets": sorted({key.split(":", 1)[0].upper() for key in candidate.ranks}),
    }


def _status_summary(statuses: list[SourceStatus]) -> tuple[int, int, int]:
    return (
        sum(status.state == "healthy" for status in statuses),
        sum(status.state == "degraded" for status in statuses),
        sum(status.state == "failed" for status in statuses),
    )


def _render_card(index: int, item: ScoredCandidate) -> str:
    candidate = item.candidate
    name = html.escape(candidate.name)
    developer = html.escape(candidate.developer or "未知开发者")
    url = html.escape(candidate.url, quote=True)
    segment_label = SEGMENT_LABELS[candidate.segment]
    source_label = _source_label(candidate.source)
    reasons = "".join(f"<li>{html.escape(reason)}</li>" for reason in item.reasons)
    markets = " · ".join(sorted({key.split(":", 1)[0].upper() for key in candidate.ranks}))
    metrics = (
        f"最佳榜位 <strong>#{candidate.best_rank}</strong>"
        f"　覆盖 <strong>{candidate.market_count}</strong> 市场"
        f"　评分 <strong>{candidate.rating:.1f}</strong>"
        f"　评价 <strong>{_compact_number(candidate.review_count)}</strong>"
    )
    component_rows = "".join(
        f"""
        <div class="component">
          <span>{html.escape(label)}</span><span>{value:.1f}/{item.component_weights[label]:.0f}</span>
          <div class="bar"><i style="width:{min(value / item.component_weights[label] * 100, 100):.0f}%"></i></div>
        </div>
        """
        for label, value in item.components.items()
        if item.component_weights[label] > 0
    )
    initial = html.escape(candidate.name[:1].upper() if candidate.name else segment_label[:1])
    image_html = f'<span>{initial}</span>'
    title_html = f'<a href="{url}">{name}</a>' if url else name
    return f"""
    <article class="card">
      <div class="rank">{index:02d}</div>
      <div class="icon">{image_html}</div>
      <div class="main">
        <div class="headline">
          <div><span class="pill">{segment_label}</span><span class="source">{source_label}</span></div>
          <div class="score">{item.score:.1f}<small>/100</small></div>
        </div>
        <h3>{title_html}</h3>
        <div class="developer">{developer}</div>
        <div class="metrics">{metrics}</div>
        <div class="markets">{html.escape(markets)}</div>
        <ul>{reasons}</ul>
        <div class="components">{component_rows}</div>
      </div>
    </article>
    """


def render_html(
    title: str,
    generated_at: datetime,
    sections: dict[str, list[ScoredCandidate]],
    statuses: list[SourceStatus],
    total_candidates: int,
    history_days: list[int],
    run_id: int,
    fingerprint: str,
) -> str:
    healthy, degraded, failed = _status_summary(statuses)
    total_selected = sum(len(items) for items in sections.values())
    section_html = ""
    for segment, items in sections.items():
        cards = "".join(_render_card(index, item) for index, item in enumerate(items, start=1))
        section_html += f"""
        <section class="ranking">
          <div class="section-title"><span>{html.escape(SEGMENT_LABELS[segment])}</span><small>独立评分 · Top {len(items)}</small></div>
          {cards}
        </section>
        """
    history_notice = ""
    if not history_days:
        history_notice = """
        <div class="notice"><strong>冷启动报告：</strong>今天建立配对基线；积累后会自动启用 1、3、7 日排名与评价增速。</div>
        """
    elif len(history_days) < 3:
        history_notice = (
            '<div class="notice"><strong>历史积累中：</strong>当前已启用 '
            + "、".join(f"{day}日" for day in history_days)
            + " 对比，其余周期将在数据足够后自动加入。</div>"
        )
    issues = [status for status in statuses if status.state in {"degraded", "failed"}]
    issues_html = ""
    if issues:
        rows = "".join(
            f"<li><strong>{html.escape(status.state)}</strong> · {html.escape(status.name)}：{html.escape(status.detail or '无完整数据')}</li>"
            for status in issues
        )
        issues_html = f'<details><summary>数据源异常或降级（{len(issues)}）</summary><ul>{rows}</ul></details>'
    generated_label = generated_at.strftime("%Y-%m-%d %H:%M %Z")
    trace = f"run #{run_id} · {fingerprint[:12]}"
    return f"""<!doctype html>
<html lang="zh-CN"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · {generated_at:%Y-%m-%d}</title>
<style>
  *{{box-sizing:border-box}} body{{margin:0;background:#f3f5f7;color:#16202a;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;line-height:1.55}}
  .wrap{{max-width:850px;margin:0 auto;padding:28px 16px 52px}} .hero{{background:linear-gradient(135deg,#0d1b2a,#173a44);color:#fff;border-radius:22px;padding:30px;box-shadow:0 16px 45px rgba(13,27,42,.18)}}
  .eyebrow{{color:#7ee0c3;font-size:12px;font-weight:800;letter-spacing:.14em;text-transform:uppercase}} h1{{font-size:30px;line-height:1.2;margin:8px 0}} .sub{{color:#c7d6da;margin:0}} .trace{{color:#86a2aa;font-size:11px;margin-top:9px}}
  .summary{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:24px}} .summary div{{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.1);border-radius:14px;padding:12px}} .summary strong{{display:block;font-size:22px}} .summary span{{font-size:12px;color:#bfd0d4}}
  .health{{font-size:12px;color:#bfd0d4;margin-top:12px}} .health b{{color:#7ee0c3}} .notice{{margin:16px 0;background:#fff6d9;color:#6d5312;border:1px solid #f1d987;border-radius:14px;padding:13px 16px}}
  .ranking{{margin-top:30px}} .section-title{{display:flex;align-items:baseline;justify-content:space-between;border-bottom:2px solid #173a44;margin:0 2px 12px;padding:0 2px 8px}} .section-title span{{font-size:23px;font-weight:900}} .section-title small{{color:#6d8088}}
  .card{{position:relative;display:grid;grid-template-columns:34px 76px 1fr;gap:14px;margin:14px 0;background:#fff;border:1px solid #e3e8eb;border-radius:18px;padding:18px;box-shadow:0 8px 24px rgba(25,45,55,.05)}}
  .rank{{font-weight:900;font-size:18px;color:#9ba9af;padding-top:4px}} .icon{{border-radius:16px;width:72px;height:72px;background:linear-gradient(145deg,#e5f2ee,#dce5ea);display:flex;align-items:center;justify-content:center;color:#2e7462;font-size:24px;font-weight:900;overflow:hidden}}
  .headline{{display:flex;justify-content:space-between;align-items:center;gap:12px}} .pill{{display:inline-block;background:#dff7ef;color:#11644e;border-radius:999px;padding:3px 9px;font-size:11px;font-weight:800}} .source{{margin-left:7px;color:#7b8b91;font-size:12px}}
  .score{{color:#0a7a5c;font-size:23px;font-weight:900}} .score small{{font-size:11px;color:#95a2a7}} h3{{font-size:19px;margin:3px 0 0}} h3 a{{color:#16202a;text-decoration:none}} .developer,.markets{{font-size:12px;color:#809097}} .metrics{{font-size:13px;margin-top:10px;color:#4a5a61}} .markets{{margin-top:2px}}
  ul{{margin:10px 0 4px;padding-left:20px;color:#35464d;font-size:13px}} .components{{display:grid;grid-template-columns:repeat(auto-fit,minmax(84px,1fr));gap:7px;margin-top:12px}} .component{{font-size:10px;color:#728187}} .component>span:nth-child(2){{float:right;font-weight:700}} .bar{{height:4px;background:#edf1f2;border-radius:9px;clear:both;margin-top:4px;overflow:hidden}} .bar i{{display:block;height:100%;background:#42b998;border-radius:9px}}
  details{{background:#fff;border:1px solid #e3e8eb;border-radius:14px;padding:12px 16px;margin-top:16px}} footer{{color:#7a8a90;font-size:12px;padding:18px 4px}} footer strong{{color:#40545b}}
  @media(max-width:640px){{.summary{{grid-template-columns:repeat(2,1fr)}} .card{{grid-template-columns:28px 58px 1fr;padding:14px;gap:9px}} .icon{{width:54px;height:54px;border-radius:12px}} .components{{grid-template-columns:repeat(2,1fr)}} .metrics{{line-height:1.8}}}}
</style></head><body><main class="wrap">
  <header class="hero">
    <div class="eyebrow">Daily Potential Radar</div><h1>{html.escape(title)}</h1>
    <p class="sub">App、手游和 Steam 游戏分榜比较；增长指标仅使用健康市场的同口径配对数据。</p>
    <div class="trace">{html.escape(trace)} · {generated_label}</div>
    <div class="summary">
      <div><strong>{total_selected}</strong><span>今日总候选</span></div>
      <div><strong>{len(sections.get('app', []))}</strong><span>App</span></div>
      <div><strong>{len(sections.get('mobile_game', []))}</strong><span>手游</span></div>
      <div><strong>{len(sections.get('steam_game', []))}</strong><span>Steam 游戏</span></div>
    </div>
    <div class="health"><b>{healthy}</b> 健康 · {degraded} 降级 · {failed} 失败</div>
  </header>
  {history_notice}{section_html}{issues_html}
  <footer>
    <p><strong>筛选范围：</strong>从 {total_candidates:,} 个跨市场候选中按分榜选出 {total_selected} 个。</p>
    <p><strong>方法：</strong>使用 1/3/7 日配对榜位、同市场评价增速、外部讨论、健康市场覆盖和新鲜度；各分榜内部做百分位标准化。公开信号不代表精确下载量或收入。</p>
  </footer>
</main></body></html>"""


def render_text(
    title: str,
    generated_at: datetime,
    sections: dict[str, list[ScoredCandidate]],
    total_candidates: int,
    history_days: list[int],
    run_id: int,
    fingerprint: str,
) -> str:
    lines = [
        title,
        generated_at.strftime("%Y-%m-%d %H:%M %Z"),
        f"run #{run_id} · {fingerprint[:12]}",
        "",
    ]
    if not history_days:
        lines.extend(["冷启动：本次建立配对基线。", ""])
    for segment, items in sections.items():
        lines.extend([f"== {SEGMENT_LABELS[segment]} Top {len(items)} ==", ""])
        for index, item in enumerate(items, start=1):
            candidate = item.candidate
            lines.append(f"{index:02d}. {candidate.name} — {item.score:.1f}/100")
            lines.append(
                f"    最佳 #{candidate.best_rank} · {candidate.market_count} 市场 · "
                f"{candidate.rating:.1f} 分 · {_compact_number(candidate.review_count)} 评价"
            )
            lines.append(f"    {'；'.join(item.reasons)}")
            if candidate.url:
                lines.append(f"    {candidate.url}")
            lines.append("")
    lines.append(f"从 {total_candidates:,} 个候选中分榜筛选。")
    return "\n".join(lines)


def write_report(
    output_dir: Path,
    title: str,
    generated_at: datetime,
    sections: dict[str, list[ScoredCandidate]],
    statuses: list[SourceStatus],
    total_candidates: int,
    history_days: list[int],
    run_id: int,
    fingerprint: str,
) -> ReportArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    html_body = render_html(
        title, generated_at, sections, statuses, total_candidates,
        history_days, run_id, fingerprint,
    )
    text_body = render_text(
        title, generated_at, sections, total_candidates, history_days, run_id, fingerprint
    )
    serialized_sections = {
        segment: [_item_dict(index, item) for index, item in enumerate(items, start=1)]
        for segment, items in sections.items()
    }
    payload = {
        "schema_version": 2,
        "run_id": run_id,
        "data_fingerprint": fingerprint,
        "title": title,
        "generated_at": generated_at.isoformat(),
        "history_days": history_days,
        "total_candidates": total_candidates,
        "source_status": [status.as_dict() for status in statuses],
        "sections": serialized_sections,
        "items": [item for values in serialized_sections.values() for item in values],
    }

    dated_html = output_dir / f"{generated_at:%Y-%m-%d}.html"
    latest_html = output_dir / "latest.html"
    latest_text = output_dir / "latest.txt"
    latest_json = output_dir / "latest.json"
    dated_html.write_text(html_body, encoding="utf-8")
    latest_html.write_text(html_body, encoding="utf-8")
    latest_text.write_text(text_body, encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ReportArtifacts(
        dated_html=dated_html,
        latest_html=latest_html,
        latest_text=latest_text,
        latest_json=latest_json,
        html_body=html_body,
        text_body=text_body,
    )
