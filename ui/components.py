"""Shared render helpers used across pages: result cards, status badges, the
step-editor table config, and the wizard stepper."""
import os

import pandas as pd
import streamlit as st

from models import TestStep
from ui.theme import (
    icon, status_micon, STATUS_COLOR, ERR_BADGE_CLASS, ASSERTION_ACTIONS,
)

# All whitelisted step actions offered in the step editor.
ALL_ACTIONS = sorted([
    "goto", "fill", "click", "check", "press",
    "wait_for_selector", "wait_for_load_state", "wait_for_timeout",
    "scroll_to", "hover", "select", "click_text",
    "check_url", "check_text", "check_element",
    "check_attribute", "check_count",
    "dismiss_modal", "iframe_switch", "iframe_exit",
    "wait_for_stable", "select_custom", "upload_file", "drag_drop",
])


def editor_config() -> dict:
    """Column config for the step-editor data_editor (rebuilt per run — the
    column_config objects are not reusable across reruns)."""
    return {
        "#":        st.column_config.NumberColumn(disabled=True, width="small"),
        "Type":     st.column_config.TextColumn(disabled=True, width="small"),
        "Action":   st.column_config.SelectboxColumn(options=ALL_ACTIONS, width="medium"),
        "Selector": st.column_config.TextColumn(width="medium"),
        "Value":    st.column_config.TextColumn(width="medium"),
        "Force":    st.column_config.CheckboxColumn(width="small"),
    }


def steps_to_df(tc) -> pd.DataFrame:
    return pd.DataFrame([
        {
            "#": i + 1,
            "Type": "Assert" if s.action in ASSERTION_ACTIONS else "Action",
            "Action": s.action,
            "Selector": s.selector or "",
            "Value": s.value or "",
            "Force": s.force,
        }
        for i, s in enumerate(tc.steps)
    ])


def df_to_steps(df) -> list:
    steps = []
    for _, row in df.iterrows():
        action = str(row.get("Action", "")).strip()
        if not action:
            continue
        steps.append(TestStep(
            action=action,
            selector=str(row.get("Selector", "")).strip() or None,
            value=str(row.get("Value", "")).strip() or None,
            force=bool(row.get("Force", False)),
        ))
    return steps


def render_error_message(ex) -> None:
    if not ex.error_message:
        return
    err_type = getattr(ex, "error_type", None)
    if err_type == "auth" or "Authentication failed" in ex.error_message:
        st.warning(
            f"**Auth failure:** {ex.error_message}\n\n"
            "Check the Login URL, username/password selectors, and credentials in Step 1."
        )
    elif err_type == "assertion":
        st.error(f"**Assertion failed:** {ex.error_message}")
    elif err_type == "timeout":
        st.error(f"**Timeout:** {ex.error_message}")
    elif err_type == "selector":
        st.error(f"**Selector not found:** {ex.error_message}")
    elif err_type == "network":
        st.error(f"**Network error:** {ex.error_message}")
    else:
        st.error(ex.error_message)


def status_badges_html(ex) -> str:
    parts = []
    css = ERR_BADGE_CLASS.get(getattr(ex, "error_type", "") or "", "badge-error")
    if ex.error_type:
        parts.append(f'<span class="badge {css}">{ex.error_type}</span>')
    if getattr(ex, "attempts", 1) > 1:
        parts.append(f'<span class="badge badge-retry">{icon("autorenew", 13)} {ex.attempts} tries</span>')
    vv = getattr(ex, "vision_verdict", None)
    if vv and not vv.get("passed", True):
        parts.append(f'<span class="badge badge-error">{icon("visibility", 13)} vision fail</span>')
    return "".join(parts)


def render_result_card(ex, tc_title: str = "") -> None:
    status_icon = status_micon(ex.status, size=20)
    color = STATUS_COLOR.get(ex.status, "#6B7280")
    t     = f"{ex.execution_time:.2f}s" if ex.execution_time is not None else "—"
    label = tc_title or ex.test_case_id
    vlab  = f" · {ex.variation_label}" if getattr(ex, "variation_label", None) else ""
    badges = status_badges_html(ex)

    with st.container(border=True):
        st.markdown(
            f'<div class="rc-header">'
            f'  {status_icon}'
            f'  <span class="rc-title" style="color:{color}">{ex.status.upper()}'
            f'    <span style="color:#1A1A1A;font-weight:500;font-size:0.88em"> — {label}{vlab}</span>'
            f'  </span>'
            f'  {badges}'
            f'  <span class="rc-time">{icon("schedule", 14, "#6B7280")} {t}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        render_error_message(ex)
        vv = getattr(ex, "vision_verdict", None)
        if vv:
            from ui.theme import mi
            v_icon = mi("check_circle") if vv.get("passed") else mi("cancel")
            conf   = vv.get("confidence", 0)
            st.caption(f"{mi('visibility')} Vision: {v_icon} confidence {conf:.0%} — {vv.get('explanation','')}")
        if ex.screenshots:
            visible = [s for s in ex.screenshots if os.path.exists(s)]
            if visible:
                cols = st.columns(min(3, len(visible)))
                for i, s in enumerate(visible):
                    cols[i % 3].image(s, use_container_width=True)


def session_error_breakdown(executions) -> dict:
    counts: dict = {}
    for ex in executions:
        if ex.status != "passed" and ex.error_type:
            counts[ex.error_type] = counts.get(ex.error_type, 0) + 1
    return counts


def stepper(labels: list, current: int) -> None:
    """Browserbase-style horizontal stepper. `current` is 1-based; earlier
    steps render as done (black with a check), later steps as muted."""
    nodes = []
    for i, label in enumerate(labels, 1):
        if i < current:
            bg, bd, content = "#000000", "#000000", icon("check", 16, "#FFFFFF")
        elif i == current:
            bg, bd, content = "#000000", "#000000", f'<span style="font-size:13px;font-weight:600;color:#FFFFFF">{i}</span>'
        else:
            bg, bd, content = "#F5F5F4", "#E8E8E4", f'<span style="font-size:13px;font-weight:600;color:#6B7280">{i}</span>'
        lab_color  = "#1A1A1A" if i <= current else "#6B7280"
        lab_weight = "600" if i == current else "500"
        nodes.append(
            f'<div style="display:flex;flex-direction:column;align-items:center;gap:6px;flex:0 0 auto;min-width:64px">'
            f'<div style="width:30px;height:30px;border-radius:9999px;background:{bg};'
            f'border:0.5px solid {bd};display:flex;align-items:center;justify-content:center">{content}</div>'
            f'<div style="font-size:11px;font-weight:{lab_weight};color:{lab_color};text-align:center;line-height:1.2">{label}</div>'
            f'</div>'
        )
        if i < len(labels):
            conn = "#000000" if i < current else "#E8E8E4"
            nodes.append(f'<div style="flex:1 1 auto;height:1px;background:{conn};margin:15px 4px 0 4px"></div>')
    st.markdown(
        '<div style="display:flex;align-items:flex-start;justify-content:space-between;margin:4px 0 28px 0">'
        + "".join(nodes) + "</div>",
        unsafe_allow_html=True,
    )
