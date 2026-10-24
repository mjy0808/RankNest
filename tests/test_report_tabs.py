from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from app_radar.models import Candidate, ScoredCandidate
from app_radar.report import write_report


class _ReportElements(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tabs: list[dict[str, str | None]] = []
        self.panels: list[dict[str, str | None]] = []
        self.section_stack: list[str | None] = []
        self.theme_locations: list[tuple[str | None, str | None]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "section":
            self.section_stack.append(
                attributes.get("id") if attributes.get("role") == "tabpanel"
                else self.section_stack[-1] if self.section_stack else None
            )
        if "themes" in (attributes.get("class") or "").split():
            self.theme_locations.append((
                self.section_stack[-1] if self.section_stack else None,
                attributes.get("data-theme-segment"),
            ))
        if attributes.get("role") == "tab":
            self.tabs.append(attributes)
        elif attributes.get("role") == "tabpanel":
            self.panels.append(attributes)

    def handle_endtag(self, tag: str) -> None:
        if tag == "section":
            self.section_stack.pop()


def preview_sections() -> dict[str, list[ScoredCandidate]]:
    sections: dict[str, list[ScoredCandidate]] = {}
    for segment, source, kind, name in (
        ("app", "apple", "app", "Focus Notes · 专注笔记"),
        ("mobile_game", "apple", "game", "Tiny Puzzle · 轻量解谜"),
        ("non_mobile_game", "steam", "game", "Repair Workshop · 维修工坊"),
    ):
        candidate = Candidate(
            source=source, external_id=segment, name=name, kind=kind,
            ranks={"us:top-free": 12}, ratings={"us": 4.7},
            review_counts={"us": 42},
            metadata={"early_candidate": True, "early_signal_score": 80},
        )
        sections[segment] = [ScoredCandidate(candidate, 75.0, {}, {}, ("早期增长信号",), None)]
    return sections


class ReportTabsTests(unittest.TestCase):
    def test_each_segment_has_linked_accessible_tabs_in_latest_and_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            artifacts = write_report(
                Path(directory), "机会日报", datetime(2026, 10, 23, tzinfo=timezone.utc),
                preview_sections(), [], 3, [], 1, "tabs-test",
            )
            for path in (artifacts.latest_html, artifacts.dated_html):
                elements = _ReportElements()
                body = path.read_text(encoding="utf-8")
                elements.feed(body)
                self.assertEqual(len(elements.tabs), 3)
                self.assertEqual(len(elements.panels), 3)
                self.assertEqual(elements.theme_locations, [
                    (f"panel-{segment}", segment) for segment in preview_sections()
                ])
                for index, (tab, panel) in enumerate(zip(elements.tabs, elements.panels)):
                    self.assertEqual(tab["aria-controls"], panel["id"])
                    self.assertEqual(panel["aria-labelledby"], tab["id"])
                    self.assertEqual(tab["aria-selected"], "true" if index == 0 else "false")
                    # No-JavaScript readers must still see every ranking.
                    self.assertNotIn("hidden", panel)
                self.assertIn("window.addEventListener('hashchange'", body)

    def test_disabled_segments_do_not_create_a_tab(self) -> None:
        sections = preview_sections()
        del sections["non_mobile_game"]
        with tempfile.TemporaryDirectory() as directory:
            artifacts = write_report(
                Path(directory), "机会日报", datetime(2026, 10, 23, tzinfo=timezone.utc),
                sections, [], 2, [], 1, "tabs-test",
            )
            elements = _ReportElements()
            elements.feed(artifacts.html_body)
            self.assertEqual(len(elements.tabs), 2)
            self.assertNotIn("tab-non_mobile_game", artifacts.html_body)
