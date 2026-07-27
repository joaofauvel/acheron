
def test_step_2_compose_start(compose_stack) -> None:
    orchestrator = compose_stack.get_json("https://localhost:8000/health")
    assert orchestrator == {"status": "ok"}, "step 2: orchestrator did not become healthy"
    dashboard = compose_stack.get_text("http://localhost:8080/")
    assert "Acheron" in dashboard, "step 2: dashboard did not render its index page"
