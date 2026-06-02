"""Preferences — saved defaults for browser, reliability, and model, plus a
session-only API-key override."""
import os

import streamlit as st

from core.config import config
from ui.prefs import save_prefs, DEFAULT_PREFS
from ui.theme import eyebrow

_BROWSERS = ["chromium", "firefox", "webkit"]
_MODELS = [
    "claude-sonnet-4-6",
    "claude-haiku-4-5",
    "claude-3-5-haiku-20241022",
    "claude-3-haiku-20240307",
]


def render(ctx) -> None:
    prefs = ctx.prefs
    eyebrow("Settings")
    st.title("Preferences")
    st.caption("Defaults applied to every new test run. Saved to `.qa_prefs.json`.")

    # ── Browser defaults ────────────────────────────────────────────────────
    st.subheader("Browser defaults")
    b1, b2, b3 = st.columns(3)
    with b1:
        browser = st.selectbox(
            "Engine", _BROWSERS,
            index=_BROWSERS.index(prefs.get("browser", "chromium"))
            if prefs.get("browser") in _BROWSERS else 0,
            key="pref_browser",
        )
    with b2:
        headless = st.checkbox("Headless", value=bool(prefs.get("headless", True)), key="pref_headless")
    with b3:
        timeout = st.number_input(
            "Timeout (ms)", value=int(prefs.get("timeout", 30000)),
            min_value=5000, max_value=120000, step=5000, key="pref_timeout",
        )

    # ── Reliability ─────────────────────────────────────────────────────────
    st.subheader("Reliability")
    r1, r2, r3 = st.columns(3)
    with r1:
        max_retries = st.slider(
            "Retry on timeout", 0, 3, value=int(prefs.get("max_retries", 0)),
            help="Retry the entire test if a step times out.", key="pref_retries",
        )
    with r2:
        use_vision = st.checkbox(
            "Vision verification", value=bool(prefs.get("use_vision", False)),
            help="Use Claude vision to verify the final screenshot against expected results.",
            key="pref_vision",
        )
    with r3:
        per_step_ss = st.checkbox(
            "Per-step screenshots", value=bool(prefs.get("per_step_screenshots", False)),
            help="Capture a screenshot after EVERY step. Useful for debugging — slower.",
            key="pref_perstep",
        )

    # ── Model ───────────────────────────────────────────────────────────────
    st.subheader("Model")
    cur_model = prefs.get("model", config.CLAUDE_MODEL)
    model_opts = _MODELS if cur_model in _MODELS else [cur_model] + _MODELS
    model = st.selectbox("Claude model", model_opts, index=model_opts.index(cur_model), key="pref_model")

    if st.button("Save preferences", type="primary"):
        save_prefs({
            "browser": browser, "headless": headless, "timeout": int(timeout),
            "max_retries": int(max_retries), "use_vision": use_vision,
            "per_step_screenshots": per_step_ss, "model": model,
        })
        os.environ["CLAUDE_MODEL"] = model  # picked up by the LLM model-list builder
        st.success("Preferences saved.", icon=":material/check_circle:")
        st.rerun()

    st.divider()

    # ── API key (session-only) ──────────────────────────────────────────────
    st.subheader("Claude API key")
    has_key = bool(config.CLAUDE_API_KEY)
    if has_key:
        masked = config.CLAUDE_API_KEY[:4] + "…" + config.CLAUDE_API_KEY[-4:]
        st.caption(f"Current key detected (`{masked}`), sourced from environment / secrets.")
    else:
        st.warning("No CLAUDE_API_KEY found — set it in `.env` / secrets, or override below.",
                   icon=":material/warning:")

    new_key = st.text_input(
        "Override key for this session", type="password",
        help="Applied to this session only — never written to disk. The durable place is `.env` / secrets.",
        key="pref_apikey",
    )
    if st.button("Apply key for this session"):
        if new_key.strip():
            os.environ["CLAUDE_API_KEY"] = new_key.strip()
            config.CLAUDE_API_KEY = new_key.strip()
            st.cache_resource.clear()   # rebuild LLM / workflow / report with the new key
            st.success("Key applied for this session.", icon=":material/check_circle:")
            st.rerun()
        else:
            st.warning("Enter a key first.")

    # ── Session usage ───────────────────────────────────────────────────────
    st.divider()
    st.subheader("Session usage")
    used = ctx.workflow.api_call_count if st.session_state.get("ready") else 0
    cap  = config.MAX_API_CALLS_PER_SESSION
    st.progress(min(used / cap, 1.0) if cap else 0, text=f"API calls: {used} / {cap}")
    st.caption(f"Defaults reset each app session. Soft cap = {cap} calls.")
