"""Overview / dashboard — KPIs + quick actions on top, recent runs + trend below."""
import pandas as pd
import streamlit as st

from core.session_state import state
from ui.theme import mi, eyebrow


def _goto(page: str, step: int | None = None):
    state.page = page
    if step is not None:
        state.wizard_step = step
    st.rerun()


def render(ctx) -> None:
    eyebrow("Dashboard")
    st.title("Overview")
    st.caption("Your testing activity at a glance.")

    db = ctx.db

    # ── Load analytics (guarded) ────────────────────────────────────────────
    history, analytics = [], {"trend": [], "error_types": {}, "flaky_tests": []}
    if db:
        try:
            history   = db.list_runs(limit=100)
            analytics = db.get_analytics(limit=60)
        except Exception as e:
            st.warning(f"Could not load analytics: {e}", icon=":material/warning:")

    total_runs   = len(history)
    total_tests  = sum(r["total"] for r in history)
    total_passed = sum(r["passed"] for r in history)
    avg_pass     = round(total_passed / total_tests * 100, 1) if total_tests else 0.0
    flaky_count  = len(analytics["flaky_tests"])

    # ── KPI strip ───────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    if db:
        k1.metric("Total Runs",        total_runs)
        k2.metric("Tests Executed",    total_tests)
        k3.metric("Avg Pass Rate",     f"{avg_pass:.1f}%")
        k4.metric("Flaky Tests (30d)", flaky_count)
    else:
        for col, lbl in zip((k1, k2, k3, k4),
                            ("Total Runs", "Tests Executed", "Avg Pass Rate", "Flaky Tests (30d)")):
            col.metric(lbl, "—")
        st.caption("Set `DATABASE_URL` to enable run history and analytics.")

    st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)

    # ── Quick actions ───────────────────────────────────────────────────────
    st.subheader("Quick actions")
    q1, q2, q3 = st.columns(3)
    with q1:
        if st.button(f"{mi('add_circle')}  Run New Test", type="primary", use_container_width=True):
            _goto("Run New Test", step=1)
    with q2:
        if st.button(f"{mi('history')}  View Run History", use_container_width=True):
            _goto("Run History")
    with q3:
        if st.button(f"{mi('tune')}  Preferences", use_container_width=True):
            _goto("Preferences")

    st.divider()

    # ── Recent runs + mini trend ────────────────────────────────────────────
    left, right = st.columns([3, 2])

    with left:
        st.subheader("Recent runs")
        if not history:
            st.caption("No runs yet — start with **Run New Test**.")
        else:
            recent = pd.DataFrame([
                {
                    "Run Name":  r["name"],
                    "Date":      r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else "—",
                    "Tests":     r["total"],
                    "Passed":    r["passed"],
                    "Pass Rate": f"{r['pass_rate']:.0f}%",
                }
                for r in history[:5]
            ])
            st.dataframe(recent, hide_index=True, use_container_width=True)

    with right:
        st.subheader("Pass rate trend")
        trend = analytics["trend"]
        if len(trend) < 2:
            st.caption("Not enough history yet — run tests on at least two different days.")
        else:
            trend_df = pd.DataFrame(trend).set_index("date")
            trend_df.index = pd.to_datetime(trend_df.index)
            trend_df = trend_df.sort_index()
            st.line_chart(trend_df[["pass_rate"]], height=180)
