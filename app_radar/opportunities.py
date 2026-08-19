from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from .models import Candidate, ScoredCandidate


THEME_LABELS = {
    "ai_automation": "AI 与自动化工具",
    "focused_utility": "高频轻工具",
    "workflow_productivity": "垂直工作流",
    "photo_video": "拍摄与内容工具",
    "education": "细分学习工具",
    "health_lifestyle": "健康与生活方式",
    "puzzle": "短回合益智",
    "idle_tycoon": "放置与经营循环",
    "repair_simulation": "维修与模拟体验",
    "card_rogue": "卡牌与可重复构筑",
    "strategy_rpg": "策略与角色成长",
    "casual_game": "轻量休闲玩法",
    "other_app": "垂直场景 App",
    "other_game": "独立玩法机会",
}


def _searchable(candidate: Candidate) -> str:
    values: list[object] = [
        candidate.name,
        candidate.developer,
        candidate.metadata.get("genre", ""),
        candidate.metadata.get("description", ""),
    ]
    genres = candidate.metadata.get("genres", [])
    if isinstance(genres, list):
        values.extend(genres)
    return " ".join(str(value).casefold() for value in values if value)


def classify_theme(candidate: Candidate) -> tuple[str, str]:
    text = _searchable(candidate)
    primary = str(candidate.metadata.get("genre", "")).casefold()
    if candidate.segment == "app":
        if any(term in text for term in (" ai ", "llm", "agent", "chatbot", "copilot")):
            key = "ai_automation"
        elif primary in {"photo & video", "graphics & design"} or any(
            term in text for term in ("camera", "photo", "video", "image", "拍照", "相机")
        ):
            key = "photo_video"
        elif primary in {"productivity", "business"} or any(
            term in text for term in ("workflow", "project", "task", "calendar", "勤怠")
        ):
            key = "workflow_productivity"
        elif primary in {"education", "reference"} or any(
            term in text for term in ("learn", "study", "quiz", "exam", "過去問")
        ):
            key = "education"
        elif primary in {"health & fitness", "lifestyle", "food & drink"}:
            key = "health_lifestyle"
        elif primary in {"utilities", "weather", "navigation"}:
            key = "focused_utility"
        else:
            key = "other_app"
    else:
        if any(term in text for term in ("puzzle", "sort", "block", "word", "jigsaw", "sudoku", "jam")):
            key = "puzzle"
        elif any(term in text for term in ("idle", "clicker", "tycoon", "factory")):
            key = "idle_tycoon"
        elif any(term in text for term in ("repair", "simulator", "simulation")):
            key = "repair_simulation"
        elif any(term in text for term in ("card", "deck", "rogue")):
            key = "card_rogue"
        elif any(term in text for term in ("strategy", "rpg", "role playing", "tactical")):
            key = "strategy_rpg"
        elif candidate.segment == "mobile_game":
            key = "casual_game"
        else:
            key = "other_game"
    return key, THEME_LABELS[key]


CARD_TEMPLATES: dict[str, dict[str, str]] = {
    "ai_automation": {
        "target_user": "愿意为一个高频任务付费、但不想学习复杂 AI 提示词的用户",
        "mvp_scope": "一个任务入口、结构化输入、可编辑结果和使用记录",
        "monetization": "免费额度 + 按月订阅或用量包",
        "differentiation": "把通用模型封装成一个可复用工作流，以结果质量和速度竞争",
    },
    "focused_utility": {
        "target_user": "每天反复完成一个明确任务、但现有工具步骤过多的用户",
        "mvp_scope": "单一主流程、快捷入口、历史记录和可导出结果",
        "monetization": "免费基础版 + 一次性 Pro 或低价订阅",
        "differentiation": "减少步骤并强化即时反馈，再针对一个职业或场景做模板",
    },
    "workflow_productivity": {
        "target_user": "仍用表格、聊天或纸笔完成垂直流程的小团队",
        "mvp_scope": "模板、状态流转、提醒、批量操作和基础协作",
        "monetization": "按席位订阅，或个人版免费、团队版付费",
        "differentiation": "只服务一个职业流程，把通用项目管理功能压缩成开箱即用模板",
    },
    "photo_video": {
        "target_user": "有固定拍摄或内容发布风格的创作者和小商家",
        "mvp_scope": "一种拍摄场景、3–5 个模板、批处理和一键分享",
        "monetization": "模板包、一次性买断或创作者订阅",
        "differentiation": "围绕一个内容风格重做从拍摄、处理到发布的完整链路",
    },
    "education": {
        "target_user": "准备特定考试、证照或细分技能训练的学习者",
        "mvp_scope": "题库、错题、短测验、进度反馈和复习提醒",
        "monetization": "题库包买断、会员订阅或机构授权",
        "differentiation": "选择更窄的考试或人群，用反馈闭环而不是堆内容",
    },
    "health_lifestyle": {
        "target_user": "需要低负担记录并持续获得反馈的细分生活方式人群",
        "mvp_scope": "快速记录、趋势反馈、提醒和可分享周报",
        "monetization": "免费记录 + 高级分析订阅",
        "differentiation": "聚焦一个高频行为，把记录成本压到十秒以内",
    },
    "puzzle": {
        "target_user": "偏好 3–8 分钟短局、规则易懂但有成长空间的休闲玩家",
        "mvp_scope": "一个核心规则、50 个关卡、难度曲线、广告与去广告付费",
        "monetization": "激励广告 + 去广告内购 + 轻量关卡包",
        "differentiation": "保留规则骨架，重做主题、关卡生成、反馈节奏和商业化",
    },
    "idle_tycoon": {
        "target_user": "喜欢低操作投入、持续数值成长和收集反馈的玩家",
        "mvp_scope": "一个生产循环、三层升级、离线收益和首周任务",
        "monetization": "激励广告 + 加速/装饰内购",
        "differentiation": "换成未被充分使用的职业题材，并缩短首次正反馈时间",
    },
    "repair_simulation": {
        "target_user": "喜欢可视化操作反馈、整理和修复过程的玩家",
        "mvp_scope": "一种物件、5–10 个操作步骤、订单循环和工具升级",
        "monetization": "买断制，或免费试玩 + 内容包",
        "differentiation": "选更窄的物件或职业，强化触感反馈和可扩展订单内容",
    },
    "card_rogue": {
        "target_user": "喜欢短周期构筑、组合发现和重复挑战的核心玩家",
        "mvp_scope": "30 张卡、3 个流派、单局循环和基础随机事件",
        "monetization": "买断制 + 后续内容包",
        "differentiation": "从一条新规则组合出发，而不是复刻已有角色、美术或卡牌",
    },
    "strategy_rpg": {
        "target_user": "喜欢长期成长与策略组合、但不需要大型在线系统的玩家",
        "mvp_scope": "一个核心战斗循环、少量角色和可重复关卡",
        "monetization": "买断制或内容章节付费",
        "differentiation": "压缩系统量，先证明单局策略深度和角色成长循环",
    },
    "casual_game": {
        "target_user": "希望快速理解、随时开始和结束一局的移动玩家",
        "mvp_scope": "一个 5 分钟核心循环、基础关卡和留存任务",
        "monetization": "激励广告 + 去广告内购",
        "differentiation": "用不同题材、反馈和关卡曲线验证同类需求",
    },
    "other_app": {
        "target_user": "在一个垂直场景中被通用产品忽略的明确人群",
        "mvp_scope": "一个端到端主流程、模板、记录和导出",
        "monetization": "免费试用 + 买断或订阅",
        "differentiation": "缩小人群和场景，用完整体验替代功能堆叠",
    },
    "other_game": {
        "target_user": "愿意为清晰核心循环和独特题材尝试独立游戏的玩家",
        "mvp_scope": "可玩 10–15 分钟的垂直切片和一个重复循环",
        "monetization": "优先买断制，验证后再扩展内容",
        "differentiation": "先验证玩法循环，再决定题材、美术和内容规模",
    },
}


def build_opportunity_card(candidate: Candidate) -> dict[str, Any]:
    key, label = classify_theme(candidate)
    template = CARD_TEMPLATES[key]
    risks = [str(value) for value in candidate.metadata.get("opportunity_risks", [])]
    if not risks:
        risks = ["需要先验证获客成本、次日留存和付费意愿"]
    return {
        "theme_key": key,
        "theme": label,
        "target_user": template["target_user"],
        "mvp_scope": template["mvp_scope"],
        "monetization": template["monetization"],
        "differentiation": template["differentiation"],
        "risks": risks[:3],
    }


def build_theme_summaries(
    sections: dict[str, list[ScoredCandidate]], limit: int = 6
) -> list[dict[str, Any]]:
    grouped: dict[str, list[ScoredCandidate]] = defaultdict(list)
    for items in sections.values():
        for item in items:
            key = str(item.candidate.metadata.get("opportunity_theme_key", ""))
            if key:
                grouped[key].append(item)
    summaries: list[dict[str, Any]] = []
    for key, items in grouped.items():
        if len(items) < 2:
            continue
        validated = sum(
            item.candidate.metadata.get("opportunity_tier") == "validated" for item in items
        )
        summaries.append(
            {
                "key": key,
                "label": THEME_LABELS.get(key, key),
                "count": len(items),
                "validated_count": validated,
                "average_score": round(mean(item.score for item in items), 1),
                "average_fit": round(
                    mean(int(item.candidate.metadata.get("opportunity_fit", 0)) for item in items),
                    1,
                ),
                "examples": [item.candidate.name for item in items[:3]],
                "signal": (
                    "多个已验证产品同时出现，适合优先做需求访谈和原型"
                    if validated >= 2
                    else "同类新产品集中出现，先加入观察并验证是否持续增长"
                ),
            }
        )
    summaries.sort(
        key=lambda value: (value["validated_count"], value["count"], value["average_score"]),
        reverse=True,
    )
    return summaries[:limit]
