"""Run History & Analytics — Overview / Trends / Flaky / Manage sub-tabs."""
import pandas as pd
import streamlit as st

from core.session_state import state
from ui.theme import mi, eyebrow


def render(ctx) -> None:
    db = ctx.db
    eyebrow("Analytics")
    st.title("Run History & Analytics")

    if not db:
        st.info("PostgreSQL not configured — run history is unavailable. Set DATABASE_URL to enable.")
        return

    h_overview, h_trends, h_flaky, h_manage = st.tabs(
        [f"{mi('bar_chart')} Overview", f"{mi('trending_up')} Trends",
         f"{mi('warning')} Flaky Tests", f"{mi('folder')} Manage Runs"]
    )

    try:
        _history   = db.list_runs(limit=100)
        _analytics = db.get_analytics(limit=60)
    except Exception as _fetch_err:
        st.error(f"History load error: {_fetch_err}")
        _history   = []
        _analytics = {"trend": [], "error_types": {}, "flaky_tests": []}

    _trend       = _analytics["trend"]
    _error_types = _analytics["error_types"]
    _flaky_tests = _analytics["flaky_tests"]

    # ── Overview ────────────────────────────────────────────────────────────
    with h_overview:
        if not _history:
            st.info("No saved runs yet. Run some tests from **Run New Test**.")
        else:
            total_runs   = len(_history)
            total_tests  = sum(r["total"]  for r in _history)
            total_passed = sum(r["passed"] for r in _history)
            avg_pass     = round(total_passed / total_tests * 100, 1) if total_tests else 0.0
            flaky_count  = len(_flaky_tests)

            ov1, ov2, ov3, ov4 = st.columns(4)
            ov1.metric("Total Runs",        total_runs)
            ov2.metric("Tests Executed",    total_tests)
            ov3.metric("Avg Pass Rate",     f"{avg_pass:.1f}%")
            ov4.metric("Flaky Tests (30d)", flaky_count)

            st.divider()
            st.subheader("Recent runs")
            hist_df = pd.DataFrame([
                {
                    "Run Name":  r["name"],
                    "Date":      r["created_at"].strftime("%Y-%m-%d %H:%M") if r["created_at"] else "—",
                    "Tests":     r["total"],
                    "Passed":    r["passed"],
                    "Failed":    r["total"] - r["passed"],
                    "Pass Rate": f"{r['pass_rate']:.0f}%",
                }
                for r in _history[:25]
            ])
            st.dataframe(hist_df, hide_index=True, use_container_width=True)

            if _error_types:
                st.divider()
                st.subheader("Failure types (last 30 days)")
                err_df = pd.DataFrame(
                    {"Error Type": list(_error_types.keys()), "Count": list(_error_types.values())}
                ).set_index("Error Type").sort_values("Count", ascending=False)
                st.bar_chart(err_df, height=200)

    # ── Trends ──────────────────────────────────────────────────────────────
    with h_trends:
        if len(_trend) < 2:
            st.info("Not enough history for trend charts yet — run tests on at least two different days.")
        else:
            trend_df = pd.DataFrame(_trend).set_index("date")
            trend_df.index = pd.to_datetime(trend_df.index)
            trend_df = trend_df.sort_index()

            st.subheader("Pass rate over time (%)")
            st.line_chart(trend_df[["pass_rate"]], height=220)

            st.subheader("Test volume per day")
            st.bar_chart(trend_df[["passed", "failed"]], height=220)

            st.subheader("Runs per day")
            st.bar_chart(trend_df[["run_count"]], height=180)

            with st.expander("Raw trend data", expanded=False):
                display_trend = trend_df.copy()
                display_trend.index = display_trend.index.strftime("%Y-%m-%d")
                st.dataframe(display_trend, use_container_width=True)

    # ── Flaky Tests ─────────────────────────────────────────────────────────
    with h_flaky:
        st.subheader("Flaky test detection (last 30 days)")
        st.caption(
            "Tests are flagged flaky when they have both passed and failed results "
            "across multiple runs. High flaky rate = unreliable test."
        )

        if not _flaky_tests:
            st.success("No flaky tests detected in the last 30 days.", icon=":material/celebration:")
        else:
            flaky_df = pd.DataFrame(_flaky_tests)
            flaky_df["Flaky Rate"] = flaky_df["flaky_rate"].apply(lambda x: f"{x:.0f}%")
            flaky_df = flaky_df.rename(columns={
                "tc_id":  "Test Case ID",
                "total":  "Total Runs",
                "passed": "Passes",
                "failed": "Failures",
            })
            flaky_df = flaky_df[["Test Case ID", "Total Runs", "Passes", "Failures", "Flaky Rate"]]
            st.dataframe(
                flaky_df, hide_index=True, use_container_width=True,
                column_config={
                    "Flaky Rate": st.column_config.TextColumn("Flaky Rate"),
                    "Failures":   st.column_config.NumberColumn("Failures"),
                },
            )
            chart_df = (
                pd.DataFrame(_flaky_tests)
                .set_index("tc_id")[["passed", "failed"]]
                .head(10)
            )
            st.subheader("Pass vs Fail counts for flaky tests")
            st.bar_chart(chart_df, height=220)

    # ── Manage Runs ─────────────────────────────────────────────────────────
    with h_manage:
        mc1, mc2, mc3 = st.columns([3, 2, 1])
        with mc1:
            search_query = st.text_input(
                f"{mi('search')} Search", placeholder="Filter by name or URL…", key="hist_search"
            )
        with mc2:
            hist_limit = st.selectbox("Show last", [10, 25, 50, 100], index=1, key="hist_limit")
        with mc3:
            st.write("")
            st.button(f"{mi('refresh')} Refresh", key="hist_refresh")

        try:
            history = db.list_runs(limit=hist_limit)
            if search_query.strip():
                q = search_query.strip().lower()
                history = [r for r in history if q in r["name"].lower()]

            if not history:
                st.info(
                    "No saved runs found."
                    + (" Try a different search term." if search_query else "")
                )
            else:
                st.markdown(f"**{len(history)} run(s)**")
                st.dataframe(
                    pd.DataFrame([
                        {
                            "ID":        r["id"],
                            "Run Name":  r["name"],
                            "Date":      r["created_at"].strftime("%Y-%m-%d %H:%M")
                                         if r["created_at"] else "—",
                            "Tests":     r["total"],
                            "Passed":    r["passed"],
                            "Pass Rate": f"{r['pass_rate']:.0f}%",
                        }
                        for r in history
                    ]),
                    hide_index=True,
                    use_container_width=True,
                )

                st.divider()
                selected_run_name = st.selectbox(
                    "Select run to manage",
                    options=[r["name"] for r in history],
                    key="hist_select",
                )
                selected_run = next((r for r in history if r["name"] == selected_run_name), None)

                if selected_run:
                    sr = selected_run
                    qa, qb, qc = st.columns(3)
                    qa.metric("Tests",     sr["total"])
                    qb.metric("Passed",    sr["passed"])
                    qc.metric("Pass Rate", f"{sr['pass_rate']:.0f}%")
                    st.markdown(
                        f"{mi('calendar_today')} {sr['created_at'].strftime('%Y-%m-%d %H:%M') if sr['created_at'] else '—'}"
                    )

                    r_col1, r_col2, r_col3 = st.columns(3)

                    with r_col1:
                        if st.button(f"{mi('restore')} Load into session", key="hist_load", type="primary"):
                            with st.spinner("Loading run…"):
                                run_data = db.load_run(selected_run["id"])
                                state.requirements = run_data["requirements"]
                                state.test_cases   = run_data["test_cases"]
                                state.executions   = run_data["executions"]
                                state.report = (
                                    {"data": run_data["report"], "url": None}
                                    if run_data["report"] else None
                                )
                                state.db_run_id = selected_run["id"]
                                state.page = "Run New Test"
                                state.wizard_step = 6 if run_data["executions"] else 4
                            st.success(f"Loaded: {selected_run['name']} — opening in the wizard…")
                            st.rerun()

                    with r_col2:
                        new_name = st.text_input(
                            "Rename run", value=selected_run["name"], key="hist_rename_input"
                        )
                        if st.button(f"{mi('save')} Save name", key="hist_rename_btn"):
                            try:
                                with db._connect() as conn:
                                    with conn.cursor() as cur:
                                        cur.execute(
                                            "UPDATE qa_runs SET name = %s WHERE id = %s",
                                            (new_name, selected_run["id"]),
                                        )
                                    conn.commit()
                                st.success("Run renamed.")
                            except Exception as e:
                                st.error(f"Rename failed: {e}")

                    with r_col3:
                        st.write("")
                        confirm_delete = st.checkbox("Confirm delete", key="hist_del_confirm")
                        if st.button(
                            f"{mi('delete')} Delete run", key="hist_delete", disabled=not confirm_delete
                        ):
                            try:
                                db.delete_run(selected_run["id"])
                                if state.db_run_id == selected_run["id"]:
                                    state.db_run_id = None
                                st.success(f"Deleted: {selected_run['name']}")
                            except Exception as e:
                                st.error(f"Delete failed: {e}")

        except Exception as manage_err:
            st.error(f"History load error: {manage_err}")
