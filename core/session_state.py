import streamlit as st

DEFAULTS: dict = {
    "requirements":         [],
    "ambiguity_scores":     [],
    "test_cases":           [],
    "selected_tests":       [],
    "executions":           [],
    "report":               None,
    "generating":           False,
    "dom_snapshot":         None,
    "db_run_id":            None,
    "design_context":       None,
    "design_discrepancies": [],
    "clarifications":       {},
    "external_test_data":   None,
    "custom_assertions":    [],
    "show_create_form":     False,
    "_show_bulk_step_form": False,
}


def init() -> None:
    for k, v in DEFAULTS.items():
        if k not in st.session_state:
            st.session_state[k] = v


class _AppState:
    @property
    def requirements(self): return st.session_state.requirements
    @requirements.setter
    def requirements(self, v):
        st.session_state.requirements = v
        st.session_state.ambiguity_scores = []

    @property
    def ambiguity_scores(self): return st.session_state.ambiguity_scores
    @ambiguity_scores.setter
    def ambiguity_scores(self, v): st.session_state.ambiguity_scores = v

    @property
    def test_cases(self): return st.session_state.test_cases
    @test_cases.setter
    def test_cases(self, v): st.session_state.test_cases = v

    @property
    def executions(self): return st.session_state.executions
    @executions.setter
    def executions(self, v): st.session_state.executions = v

    @property
    def report(self): return st.session_state.report
    @report.setter
    def report(self, v): st.session_state.report = v

    @property
    def generating(self): return st.session_state.generating
    @generating.setter
    def generating(self, v): st.session_state.generating = v

    @property
    def dom_snapshot(self): return st.session_state.dom_snapshot
    @dom_snapshot.setter
    def dom_snapshot(self, v): st.session_state.dom_snapshot = v

    @property
    def db_run_id(self): return st.session_state.db_run_id
    @db_run_id.setter
    def db_run_id(self, v): st.session_state.db_run_id = v

    @property
    def design_context(self): return st.session_state.design_context
    @design_context.setter
    def design_context(self, v): st.session_state.design_context = v

    @property
    def design_discrepancies(self): return st.session_state.design_discrepancies
    @design_discrepancies.setter
    def design_discrepancies(self, v): st.session_state.design_discrepancies = v

    @property
    def clarifications(self): return st.session_state.clarifications
    @clarifications.setter
    def clarifications(self, v): st.session_state.clarifications = v

    @property
    def external_test_data(self): return st.session_state.external_test_data
    @external_test_data.setter
    def external_test_data(self, v): st.session_state.external_test_data = v

    @property
    def custom_assertions(self): return st.session_state.custom_assertions
    @custom_assertions.setter
    def custom_assertions(self, v): st.session_state.custom_assertions = v

    @property
    def show_create_form(self): return st.session_state.show_create_form
    @show_create_form.setter
    def show_create_form(self, v): st.session_state.show_create_form = v

    @property
    def show_bulk_step_form(self): return st.session_state._show_bulk_step_form
    @show_bulk_step_form.setter
    def show_bulk_step_form(self, v): st.session_state._show_bulk_step_form = v


state = _AppState()
