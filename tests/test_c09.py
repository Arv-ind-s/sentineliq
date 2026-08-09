from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_ROOT = ROOT / "ui" / "src"
APP_SOURCE = (UI_ROOT / "App.tsx").read_text(encoding="utf-8")
SCREEN_SOURCE = (UI_ROOT / "screens" / "screens.tsx").read_text(encoding="utf-8")
TRACE_SOURCE = (UI_ROOT / "components" / "AgentTraceTimeline.tsx").read_text(encoding="utf-8")
THEME_SOURCE = (UI_ROOT / "styles" / "theme.css").read_text(encoding="utf-8")


def test_c09_adds_the_required_motion_primitives() -> None:
    assert "AnimatePresence" in SCREEN_SOURCE
    assert 'duration: reducedMotion ? 0 : 0.24' in SCREEN_SOURCE
    assert "height: 0" in SCREEN_SOURCE
    assert "useSpring(risk, { stiffness: 120, damping: 20 })" in SCREEN_SOURCE
    assert "const avoidedCost = predictivePolicy?.avoided_cost_inr ?? 0;" in SCREEN_SOURCE
    assert "staggerChildren: 0.04" in SCREEN_SOURCE
    assert "layoutId={`machine-${machine.id}`}" in SCREEN_SOURCE
    assert "<MotionConfig reducedMotion=\"user\">" in APP_SOURCE
    assert '<LayoutGroup id="machine-navigation">' in APP_SOURCE
    assert "motion.path" in TRACE_SOURCE
    assert "pathLength={1}" in TRACE_SOURCE
    assert "duration: reducedMotion ? 0 : 0.36" in TRACE_SOURCE


def test_c09_keeps_animation_budget_and_reduced_motion_css() -> None:
    numeric_durations = [
        float(value)
        for source in (SCREEN_SOURCE, TRACE_SOURCE)
        for value in re.findall(r"duration:\s*(?:reducedMotion\s*\?\s*0\s*:\s*)?(0\.\d+|\d+)", source)
    ]

    assert numeric_durations
    assert max(numeric_durations) <= 0.6
    assert "@media (prefers-reduced-motion: reduce)" in THEME_SOURCE
    assert "animation-duration: 0.01ms" in THEME_SOURCE
    assert "transition-duration: 0.01ms" in THEME_SOURCE
    assert "stream-pulse 0.6s" in THEME_SOURCE
    assert "skeleton-shimmer 0.6s" in THEME_SOURCE
    assert "schematic-pulse 0.6s" in THEME_SOURCE
    assert "1.2s" not in THEME_SOURCE
    assert "1.4s" not in THEME_SOURCE
    assert "2.2s" not in THEME_SOURCE
