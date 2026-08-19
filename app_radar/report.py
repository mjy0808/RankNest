from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import ScoredCandidate, SourceStatus
from .scoring import SEGMENT_LABELS


@dataclass(frozen=True)
class ReportArtifacts:
    dated_html: Path
    dated_json: Path
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


def _rank_change(rank: int, item: ScoredCandidate) -> int | None:
    if item.previous is None or item.previous.selected_rank is None:
        return None
    return item.previous.selected_rank - rank


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
        "previous_selected_rank": item.previous.selected_rank if item.previous else None,
        "rank_change": _rank_change(rank, item),
        "selected_streak": int(candidate.metadata.get("selected_streak", 1)),
        "opportunity_fit": int(candidate.metadata.get("opportunity_fit", 0)),
        "opportunity_tags": list(candidate.metadata.get("opportunity_tags", [])),
        "opportunity_risks": list(candidate.metadata.get("opportunity_risks", [])),
        "opportunity_dimensions": dict(
            candidate.metadata.get("opportunity_dimensions", {})
        ),
        "opportunity_tier": str(candidate.metadata.get("opportunity_tier", "watch")),
        "opportunity_theme": str(candidate.metadata.get("opportunity_theme", "")),
        "confidence_score": int(candidate.metadata.get("confidence_score", 0)),
        "confidence_label": str(candidate.metadata.get("confidence_label", "待观察")),
        "history_horizons": list(candidate.metadata.get("history_horizons", [])),
        "growth_horizons": list(candidate.metadata.get("growth_horizons", [])),
        "first_seen_days": int(candidate.metadata.get("first_seen_days", 0)),
        "early_candidate": bool(candidate.metadata.get("early_candidate", False)),
        "early_signal_score": int(candidate.metadata.get("early_signal_score", 0)),
        "early_reasons": list(candidate.metadata.get("early_reasons", [])),
        "competition_score": int(candidate.metadata.get("competition_score", 0)),
        "competition_level": str(candidate.metadata.get("competition_level", "")),
        "theme_mature_competitors": int(
            candidate.metadata.get("theme_mature_competitors", 0)
        ),
        "opportunity_card": dict(candidate.metadata.get("opportunity_card", {})),
        "build_angle": str(candidate.metadata.get("build_angle", "")),
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
    image_url = html.escape(candidate.image_url, quote=True)
    image_html = f'<span>{initial}</span>'
    if image_url:
        image_html += (
            f'<img src="{image_url}" alt="" loading="lazy" '
            'referrerpolicy="no-referrer" onerror="this.remove()">'
        )
    change = _rank_change(index, item)
    if item.previous is None or item.previous.selected_rank is None:
        trend_badge = '<span class="trend new">首次上榜</span>'
    elif change > 0:
        trend_badge = f'<span class="trend up">↑ {change}</span>'
    elif change < 0:
        trend_badge = f'<span class="trend down">↓ {abs(change)}</span>'
    else:
        trend_badge = '<span class="trend flat">— 持平</span>'
    streak = int(candidate.metadata.get("selected_streak", 1))
    streak_badge = f'<span class="streak">连续 {streak} 天</span>' if streak > 1 else ""
    opportunity_fit = int(candidate.metadata.get("opportunity_fit", 0))
    opportunity_tags = [
        html.escape(str(value))
        for value in candidate.metadata.get("opportunity_tags", [])
    ]
    opportunity_html = (
        f'<div class="opportunity"><strong>可借鉴度 {opportunity_fit}</strong>'
        + "".join(f"<span>{value}</span>" for value in opportunity_tags)
        + "</div>"
    )
    tier = str(candidate.metadata.get("opportunity_tier", "watch"))
    confidence = int(candidate.metadata.get("confidence_score", 0))
    early_candidate = bool(candidate.metadata.get("early_candidate", False))
    early_score = int(candidate.metadata.get("early_signal_score", 0))
    competition_level = html.escape(
        str(candidate.metadata.get("competition_level", "未知竞争"))
    )
    competition_score = int(candidate.metadata.get("competition_score", 0))
    if early_candidate:
        tier_class = "early"
        tier_label = f"早期苗头 · 早期 {early_score}"
    elif tier == "validated":
        tier_class = "validated"
        tier_label = f"已验证机会 · 置信 {confidence}"
    else:
        tier_class = "watch"
        tier_label = f"新发现观察 · 置信 {confidence}"
    tier_badge = (
        f'<span class="tier {tier_class}">{tier_label}</span>'
    )
    early_html = (
        f'<div class="early-metrics"><strong>早期信号 {early_score}</strong>'
        f'<span>{competition_level} {competition_score}</span></div>'
        if early_candidate
        else f'<div class="early-metrics muted"><span>{competition_level} {competition_score}</span></div>'
    )
    opportunity_card = candidate.metadata.get("opportunity_card", {})
    if not isinstance(opportunity_card, dict):
        opportunity_card = {}
    card_rows = "".join(
        f'<div><strong>{label}</strong><span>{html.escape(str(opportunity_card.get(key, "")))}</span></div>'
        for key, label in (
            ("target_user", "目标用户"),
            ("mvp_scope", "MVP"),
            ("monetization", "变现"),
            ("differentiation", "差异化"),
        )
        if opportunity_card.get(key)
    )
    risks = opportunity_card.get("risks", [])
    if not isinstance(risks, list):
        risks = []
    risk_html = (
        '<div class="execution-risk"><strong>先验证：</strong>'
        + "；".join(html.escape(str(value)) for value in risks)
        + "</div>"
        if risks
        else ""
    )
    action_html = f'<div class="action-card">{card_rows}</div>{risk_html}'
    title_html = f'<a href="{url}">{name}</a>' if url else name
    return f"""
    <article class="card">
      <div class="rank">{index:02d}</div>
      <div class="icon">{image_html}</div>
      <div class="main">
        <div class="headline">
          <div><span class="pill">{segment_label}</span><span class="source">{source_label}</span>{tier_badge}{trend_badge}{streak_badge}</div>
          <div class="score">{item.score:.1f}<small>/100</small></div>
        </div>
        <h3>{title_html}</h3>
        <div class="developer">{developer}</div>
        <div class="metrics">{metrics}</div>
        <div class="markets">{html.escape(markets)}</div>
        {opportunity_html}
        {early_html}
        {action_html}
        <ul>{reasons}</ul>
        <div class="components">{component_rows}</div>
      </div>
    </article>
    """


def _render_themes(themes: list[dict[str, Any]]) -> str:
    if not themes:
        return ""
    cards = "".join(
        f"""
        <div class="theme-card">
          <div><strong>{html.escape(str(theme.get('label', '')))}</strong><span>{int(theme.get('count', 0))} 个机会 · {int(theme.get('validated_count', 0))} 个已验证</span></div>
          <p>{html.escape(' · '.join(str(value) for value in theme.get('examples', [])))}</p>
          <small>{html.escape(str(theme.get('signal', '')))}</small>
        </div>
        """
        for theme in themes
    )
    return f'<section class="themes"><div class="section-title"><span>机会主题</span><small>同类信号聚合</small></div><div class="theme-grid">{cards}</div></section>'


def _render_backtest(backtest: dict[str, Any]) -> str:
    horizons = backtest.get("horizons", {}) if isinstance(backtest, dict) else {}
    if not isinstance(horizons, dict) or not horizons:
        return '<div class="validation waiting"><strong>命中率回测积累中：</strong>满 7 天后开始显示过去推荐是否继续增长。</div>'
    cards = "".join(
        f"""
        <div><strong>{html.escape(str(day))} 日</strong><b>{float(metric.get('confirmation_rate', 0)):.0f}%</b><span>{int(metric.get('confirmed', 0))}/{int(metric.get('total', 0))} 个继续增长 · {float(metric.get('retention_rate', 0)):.0f}% 仍在榜</span></div>
        """
        for day, metric in sorted(horizons.items(), key=lambda value: int(value[0]))
        if isinstance(metric, dict)
    )
    return f'<section class="validation"><div class="section-title"><span>历史命中率</span><small>无前视回测</small></div><div class="validation-grid">{cards}</div><p>{html.escape(str(backtest.get("definition", "")))}</p></section>'


def render_html(
    title: str,
    generated_at: datetime,
    sections: dict[str, list[ScoredCandidate]],
    statuses: list[SourceStatus],
    total_candidates: int,
    history_days: list[int],
    run_id: int,
    fingerprint: str,
    themes: list[dict[str, Any]] | None = None,
    backtest: dict[str, Any] | None = None,
    history_href: str = "archive/",
    latest_href: str | None = None,
) -> str:
    healthy, degraded, failed = _status_summary(statuses)
    total_selected = sum(len(items) for items in sections.values())
    validated_count = sum(
        item.candidate.metadata.get("opportunity_tier") == "validated"
        and not item.candidate.metadata.get("early_candidate")
        for items in sections.values()
        for item in items
    )
    early_count = sum(
        bool(item.candidate.metadata.get("early_candidate"))
        for items in sections.values()
        for item in items
    )
    watch_count = total_selected - validated_count - early_count
    section_html = ""
    for segment, items in sections.items():
        positioned = list(enumerate(items, start=1))
        early = [value for value in positioned if value[1].candidate.metadata.get("early_candidate")]
        validated = [
            value for value in positioned
            if not value[1].candidate.metadata.get("early_candidate")
            and value[1].candidate.metadata.get("opportunity_tier") == "validated"
        ]
        watch = [
            value for value in positioned
            if not value[1].candidate.metadata.get("early_candidate")
            and value[1].candidate.metadata.get("opportunity_tier") != "validated"
        ]
        groups = []
        if early:
            groups.append(
                f'<div class="tier-title early">早期苗头 <span>{len(early)}</span></div>'
                + "".join(_render_card(index, item) for index, item in early)
            )
        if validated:
            groups.append(
                f'<div class="tier-title validated">已验证机会 <span>{len(validated)}</span></div>'
                + "".join(_render_card(index, item) for index, item in validated)
            )
        if watch:
            groups.append(
                f'<div class="tier-title watch">新发现观察 <span>{len(watch)}</span></div>'
                + "".join(_render_card(index, item) for index, item in watch)
            )
        cards = "".join(groups)
        section_html += f"""
        <section class="ranking">
          <div class="section-title"><span>{html.escape(SEGMENT_LABELS[segment])}</span><small>独立评分 · Top {len(items)}</small></div>
          {cards}
        </section>
        """
    themes_html = _render_themes(themes or [])
    backtest_html = _render_backtest(backtest or {})
    history_notice = ""
    if not history_days:
        history_notice = """
        <div class="notice"><strong>冷启动报告：</strong>今天建立配对基线；积累后会自动启用 1、3、7、14、30 日趋势与命中率回测。</div>
        """
    elif len(history_days) < 5:
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
    navigation = f'<a href="{html.escape(history_href, quote=True)}">历史日报</a>'
    if latest_href:
        navigation += f'<a href="{html.escape(latest_href, quote=True)}">返回最新</a>'
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
  .ranking,.themes,.validation{{margin-top:30px}} .section-title{{display:flex;align-items:baseline;justify-content:space-between;border-bottom:2px solid #173a44;margin:0 2px 12px;padding:0 2px 8px}} .section-title span{{font-size:23px;font-weight:900}} .section-title small{{color:#6d8088}}
  .card{{position:relative;display:grid;grid-template-columns:34px 76px 1fr;gap:14px;margin:14px 0;background:#fff;border:1px solid #e3e8eb;border-radius:18px;padding:18px;box-shadow:0 8px 24px rgba(25,45,55,.05)}}
  .nav{{display:flex;gap:10px;margin-top:18px}} .nav a{{color:#d8fff3;border:1px solid rgba(255,255,255,.25);border-radius:999px;padding:6px 12px;text-decoration:none;font-size:12px;font-weight:700}}
  .rank{{font-weight:900;font-size:18px;color:#9ba9af;padding-top:4px}} .icon{{position:relative;border-radius:16px;width:72px;height:72px;background:linear-gradient(145deg,#e5f2ee,#dce5ea);display:flex;align-items:center;justify-content:center;color:#2e7462;font-size:24px;font-weight:900;overflow:hidden}} .icon img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;background:#eef3f3}}
  .headline{{display:flex;justify-content:space-between;align-items:center;gap:12px}} .pill{{display:inline-block;background:#dff7ef;color:#11644e;border-radius:999px;padding:3px 9px;font-size:11px;font-weight:800}} .source{{margin-left:7px;color:#7b8b91;font-size:12px}} .trend,.streak,.tier{{display:inline-block;margin-left:6px;border-radius:999px;padding:2px 7px;font-size:10px;font-weight:800}} .tier.early{{background:#e8e2ff;color:#513b95}} .tier.validated{{background:#d8f5e8;color:#11633f}} .tier.watch{{background:#fff0c2;color:#765500}} .trend.new{{background:#fff0c2;color:#765500}} .trend.up{{background:#dcf7e7;color:#17643a}} .trend.down{{background:#ffe2e2;color:#9b3333}} .trend.flat,.streak{{background:#edf1f2;color:#637279}}
  .score{{color:#0a7a5c;font-size:23px;font-weight:900}} .score small{{font-size:11px;color:#95a2a7}} h3{{font-size:19px;margin:3px 0 0}} h3 a{{color:#16202a;text-decoration:none}} .developer,.markets{{font-size:12px;color:#809097}} .metrics{{font-size:13px;margin-top:10px;color:#4a5a61}} .markets{{margin-top:2px}} .opportunity{{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin-top:9px}} .opportunity strong{{color:#0a7a5c;font-size:12px}} .opportunity span{{background:#eef8f5;color:#326759;border-radius:999px;padding:2px 7px;font-size:10px}} .early-metrics{{display:flex;gap:8px;margin-top:6px;color:#513b95;font-size:11px}} .early-metrics span{{color:#7a6aa8}} .early-metrics.muted{{color:#7b8b91}} .action-card{{display:grid;grid-template-columns:repeat(2,1fr);gap:6px;margin-top:9px}} .action-card div{{background:#f6f9fa;border-left:3px solid #42b998;border-radius:5px;padding:7px 9px;font-size:11px}} .action-card strong{{display:block;color:#11644e}} .action-card span{{color:#4a5a61}} .execution-risk{{font-size:11px;color:#825629;background:#fff7e8;border-radius:6px;padding:6px 9px;margin-top:6px}}
  .tier-title{{display:flex;justify-content:space-between;margin:18px 2px 4px;border-radius:10px;padding:8px 12px;font-size:13px;font-weight:900}} .tier-title.early{{background:#eee9ff;color:#513b95}} .tier-title.validated{{background:#ddf6ec;color:#11633f}} .tier-title.watch{{background:#fff3cf;color:#735600}} .theme-grid,.validation-grid{{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}} .theme-card,.validation-grid>div{{background:#fff;border:1px solid #e3e8eb;border-radius:14px;padding:14px}} .theme-card>div{{display:flex;justify-content:space-between;gap:8px}} .theme-card span,.theme-card small,.validation-grid span{{color:#718188;font-size:11px}} .theme-card p{{font-size:12px;margin:7px 0}} .validation-grid>div{{display:grid;grid-template-columns:auto auto;gap:2px 10px}} .validation-grid b{{color:#0a7a5c;font-size:22px;text-align:right}} .validation-grid span{{grid-column:1/-1}} .validation>p{{color:#809097;font-size:11px}} .validation.waiting{{margin-top:16px;background:#eef5f7;border-radius:14px;padding:13px 16px;color:#51656d}}
  ul{{margin:10px 0 4px;padding-left:20px;color:#35464d;font-size:13px}} .components{{display:grid;grid-template-columns:repeat(auto-fit,minmax(84px,1fr));gap:7px;margin-top:12px}} .component{{font-size:10px;color:#728187}} .component>span:nth-child(2){{float:right;font-weight:700}} .bar{{height:4px;background:#edf1f2;border-radius:9px;clear:both;margin-top:4px;overflow:hidden}} .bar i{{display:block;height:100%;background:#42b998;border-radius:9px}}
  details{{background:#fff;border:1px solid #e3e8eb;border-radius:14px;padding:12px 16px;margin-top:16px}} footer{{color:#7a8a90;font-size:12px;padding:18px 4px}} footer strong{{color:#40545b}}
  @media(max-width:640px){{.summary{{grid-template-columns:repeat(2,1fr)}} .card{{grid-template-columns:28px 58px 1fr;padding:14px;gap:9px}} .icon{{width:54px;height:54px;border-radius:12px}} .components,.action-card,.theme-grid,.validation-grid{{grid-template-columns:1fr}} .metrics{{line-height:1.8}}}}
</style></head><body><main class="wrap">
  <header class="hero">
    <div class="eyebrow">Daily Potential Radar</div><h1>{html.escape(title)}</h1>
    <p class="sub">同时追踪早期苗头与已验证增长，优先寻找尚未高度饱和、实现边界清晰且适合小团队切入的机会。</p>
    <div class="trace">{html.escape(trace)} · {generated_label}</div>
    <div class="summary">
      <div><strong>{total_candidates:,}</strong><span>监控候选</span></div>
      <div><strong>{len(sections.get('app', []))}</strong><span>App</span></div>
      <div><strong>{len(sections.get('mobile_game', []))}</strong><span>手游</span></div>
      <div><strong>{len(sections.get('steam_game', []))}</strong><span>Steam 游戏</span></div>
    </div>
    <div class="health"><b>{early_count} 个早期苗头</b> · {validated_count} 个已验证机会 · {watch_count} 个普通观察　|　{healthy} 个健康数据源 · {degraded} 降级 · {failed} 失败</div>
    <nav class="nav">{navigation}</nav>
  </header>
  {history_notice}{backtest_html}{themes_html}{section_html}{issues_html}
  <footer>
    <p><strong>筛选范围：</strong>从 {total_candidates:,} 个跨市场候选中按分榜选出 {total_selected} 个。</p>
    <p><strong>方法：</strong>每个分榜固定保留早期名额，综合上线/待上线时间、首次发现、低评价基数、短期抬升和多市场露头；同主题成熟竞品越多，竞争分越高并自动降权。其余候选继续使用 1/3/7/14/30 日增速、置信度和可借鉴性。政府专属、高合规、博彩、成熟 IP、强网络效应与大型制作产品使用硬门槛排除。只借鉴需求、机制和商业模式，不复制受保护内容。</p>
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
    themes: list[dict[str, Any]] | None = None,
    backtest: dict[str, Any] | None = None,
) -> str:
    lines = [
        title,
        generated_at.strftime("%Y-%m-%d %H:%M %Z"),
        f"run #{run_id} · {fingerprint[:12]}",
        "",
    ]
    if not history_days:
        lines.extend(["冷启动：本次建立配对基线。", ""])
    if backtest and backtest.get("horizons"):
        lines.append("== 历史命中率 ==")
        for horizon, metric in backtest["horizons"].items():
            lines.append(
                f"{horizon} 日：{metric['confirmation_rate']:.0f}% 继续增长 "
                f"({metric['confirmed']}/{metric['total']})"
            )
        lines.append("")
    if themes:
        lines.extend(["== 机会主题 ==", ""])
        for theme in themes:
            lines.append(
                f"- {theme['label']}：{theme['count']} 个机会，"
                f"{theme['validated_count']} 个已验证"
            )
        lines.append("")
    for segment, items in sections.items():
        lines.extend([f"== {SEGMENT_LABELS[segment]} Top {len(items)} ==", ""])
        for index, item in enumerate(items, start=1):
            candidate = item.candidate
            tier = (
                f"早期{int(candidate.metadata.get('early_signal_score', 0))}"
                if candidate.metadata.get("early_candidate")
                else "已验证" if candidate.metadata.get("opportunity_tier") == "validated"
                else "观察"
            )
            confidence = int(candidate.metadata.get("confidence_score", 0))
            lines.append(
                f"{index:02d}. [{tier}·置信{confidence}] {candidate.name} — {item.score:.1f}/100"
            )
            lines.append(
                f"    最佳 #{candidate.best_rank} · {candidate.market_count} 市场 · "
                f"{candidate.rating:.1f} 分 · {_compact_number(candidate.review_count)} 评价"
            )
            lines.append(f"    {'；'.join(item.reasons)}")
            card = candidate.metadata.get("opportunity_card", {})
            if isinstance(card, dict) and card.get("mvp_scope"):
                lines.append(f"    MVP：{card['mvp_scope']}")
                lines.append(f"    变现：{card.get('monetization', '')}")
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
    themes: list[dict[str, Any]] | None = None,
    backtest: dict[str, Any] | None = None,
    retention_days: int = 45,
) -> ReportArtifacts:
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = output_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    html_body = render_html(
        title, generated_at, sections, statuses, total_candidates,
        history_days, run_id, fingerprint, themes=themes, backtest=backtest,
    )
    archive_html_body = render_html(
        title, generated_at, sections, statuses, total_candidates,
        history_days, run_id, fingerprint, themes=themes, backtest=backtest,
        history_href="./", latest_href="../",
    )
    text_body = render_text(
        title, generated_at, sections, total_candidates, history_days, run_id, fingerprint,
        themes=themes, backtest=backtest,
    )
    serialized_sections = {
        segment: [_item_dict(index, item) for index, item in enumerate(items, start=1)]
        for segment, items in sections.items()
    }
    payload = {
        "schema_version": 5,
        "run_id": run_id,
        "data_fingerprint": fingerprint,
        "title": title,
        "generated_at": generated_at.isoformat(),
        "history_days": history_days,
        "total_candidates": total_candidates,
        "source_status": [status.as_dict() for status in statuses],
        "themes": themes or [],
        "backtest": backtest or {},
        "sections": serialized_sections,
        "items": [item for values in serialized_sections.values() for item in values],
    }

    dated_html = archive_dir / f"{generated_at:%Y-%m-%d}.html"
    dated_json = archive_dir / f"{generated_at:%Y-%m-%d}.json"
    latest_html = output_dir / "latest.html"
    latest_text = output_dir / "latest.txt"
    latest_json = output_dir / "latest.json"
    dated_html.write_text(archive_html_body, encoding="utf-8")
    dated_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    latest_html.write_text(html_body, encoding="utf-8")
    latest_text.write_text(text_body, encoding="utf-8")
    latest_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_archive_index(archive_dir, title, generated_at, retention_days)
    return ReportArtifacts(
        dated_html=dated_html,
        dated_json=dated_json,
        latest_html=latest_html,
        latest_text=latest_text,
        latest_json=latest_json,
        html_body=html_body,
        text_body=text_body,
    )


def _write_archive_index(
    archive_dir: Path, title: str, generated_at: datetime, retention_days: int
) -> None:
    cutoff = generated_at.date() - timedelta(days=retention_days)
    reports: list[tuple[date, Path]] = []
    for path in archive_dir.glob("????-??-??.html"):
        try:
            report_day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if report_day < cutoff:
            path.unlink()
            json_path = path.with_suffix(".json")
            if json_path.exists():
                json_path.unlink()
            continue
        reports.append((report_day, path))
    reports.sort(reverse=True)
    link_rows: list[str] = []
    for index, (day, path) in enumerate(reports):
        json_path = path.with_suffix(".json")
        json_link = (
            f' · <a class="json" href="{html.escape(json_path.name, quote=True)}">JSON</a>'
            if json_path.exists()
            else ""
        )
        link_rows.append(
            f'<li><a href="{html.escape(path.name, quote=True)}">{day:%Y-%m-%d}</a>'
            f'<span>{"最新" if index == 0 else "日报快照"}{json_link}</span></li>'
        )
    links = "".join(link_rows)
    body = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} · 历史日报</title><style>
body{{margin:0;background:#f3f5f7;color:#16202a;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",sans-serif}}main{{max-width:720px;margin:0 auto;padding:32px 16px}}header{{background:#173a44;color:#fff;border-radius:20px;padding:26px}}h1{{margin:4px 0 8px}}header a{{color:#8ff0d3}}ul{{list-style:none;padding:0}}li{{display:flex;justify-content:space-between;background:#fff;border:1px solid #e3e8eb;border-radius:13px;margin:9px 0;padding:14px 16px}}li a{{color:#11644e;font-weight:800;text-decoration:none}}li span{{color:#809097;font-size:12px}}li .json{{font-size:11px;color:#557178}}
</style></head><body><main><header><small>REPORT ARCHIVE</small><h1>历史日报</h1><a href="../">返回最新报告</a></header><ul>{links}</ul></main></body></html>"""
    (archive_dir / "index.html").write_text(body, encoding="utf-8")
