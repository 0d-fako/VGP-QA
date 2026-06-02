"""Service wiring for the UI.

Builds (and caches) the long-lived services and bundles them into an `AppContext`
that every page receives. Keeps `app.py` thin and pages free of import wiring.
"""
from dataclasses import dataclass
from typing import Optional

import streamlit as st

from core.config import config
from repositories.azure_storage import AzureStorageManager, LocalStorageManager
from repositories.db import DatabaseManager
from services.llm_processor import LLMProcessor
from services.workflow_service import WorkflowService
from services.report_service import ReportService
from ui.prefs import load_prefs


@st.cache_resource(show_spinner=False)
def _get_storage():
    if config.AZURE_STORAGE_CONNECTION_STRING:
        return AzureStorageManager()
    return LocalStorageManager()


@st.cache_resource(show_spinner=False)
def _get_db():
    if not DatabaseManager.is_configured():
        return None
    try:
        return DatabaseManager()
    except Exception:
        return None


@st.cache_resource(show_spinner=False)
def _get_llm():
    return LLMProcessor()


@st.cache_resource(show_spinner=False)
def _get_workflow() -> WorkflowService:
    return WorkflowService(_get_llm(), _get_storage())


@st.cache_resource(show_spinner=False)
def _get_report_service() -> ReportService:
    return ReportService(_get_llm(), _get_storage(), _get_db())


@dataclass
class AppContext:
    workflow:   WorkflowService
    storage:    object
    db:         Optional[DatabaseManager]
    report_svc: ReportService
    llm:        LLMProcessor
    prefs:      dict


def build_context() -> Optional[AppContext]:
    """Validate config and assemble the services context.

    On success sets ``st.session_state.ready = True`` and returns the context.
    On failure reports the error, sets ``ready = False`` and returns ``None``.
    """
    try:
        config.validate()
        ctx = AppContext(
            workflow=_get_workflow(),
            storage=_get_storage(),
            db=_get_db(),
            report_svc=_get_report_service(),
            llm=_get_llm(),
            prefs=load_prefs(),
        )
        if ctx.db is None and DatabaseManager.is_configured():
            st.warning("DB connection failed (history disabled).", icon=":material/warning:")
        st.session_state.ready = True
        return ctx
    except Exception as e:
        st.error(f"Initialisation error: {e}")
        st.session_state.ready = False
        return None
