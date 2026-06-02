"""Left sidebar: brand wordmark, page navigation, and session API usage."""
import streamlit as st

from core.config import config
from core.session_state import state
from ui.theme import mi

# (page label, material icon)
NAV = [
    ("Overview",     "dashboard"),
    ("Run New Test", "add_circle"),
    ("Run History",  "history"),
    ("Preferences",  "tune"),
]

_SIDEBAR_CSS = """
<style>
/* Left-align sidebar nav buttons so they read like links */
[data-testid="stSidebar"] .stButton > button {
    justify-content: flex-start !important;
    text-align: left !important;
    padding-left: 12px !important;
    margin-bottom: 2px !important;
}
[data-testid="stSidebar"] .stButton > button p { text-align: left !important; }
</style>
"""


def render_sidebar(ctx) -> None:
    with st.sidebar:
        st.markdown(_SIDEBAR_CSS, unsafe_allow_html=True)
        st.markdown(
            '<div style="font-size:18px;color:#6B7280;font-weight:700;letter-spacing:-0.02em;padding:2px 0 0 0">Quiv Agent</div>'
            '<p style="font-size:11px;color:#6B7280;margin:0 0 18px 0;letter-spacing:.04em;text-transform:uppercase">Automated E2E Testing</p>',
            unsafe_allow_html=True,
        )

        for label, ic in NAV:
            active = state.page == label
            if st.button(
                f"{mi(ic)}  {label}",
                key=f"nav_{label}",
                use_container_width=True,
                type="primary" if active else "secondary",
            ):
                if state.page != label:
                    state.page = label
                    st.rerun()

        # ── Session API usage (bottom) ──────────────────────────────────────
        st.markdown("<div style='height:20px'></div>", unsafe_allow_html=True)
        st.divider()
        used = ctx.workflow.api_call_count if st.session_state.get("ready") else 0
        cap  = config.MAX_API_CALLS_PER_SESSION
        pct  = min(used / cap, 1.0) if cap else 0
        st.caption("Session API usage")
        st.progress(pct, text=f"{used} / {cap}")
        if used >= cap:
            st.caption("Limit reached — refresh to reset.")
