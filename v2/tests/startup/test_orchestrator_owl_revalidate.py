import inspect
from stackowl.startup import orchestrator


def test_orchestrator_calls_revalidate_agent_owls_after_personas():
    src = inspect.getsource(orchestrator)
    assert "revalidate_agent_owls" in src, "boot must re-clamp agent owls"
    i_personas = src.index("register_builtin_personas")
    i_reval = src.index("revalidate_agent_owls")
    assert i_personas < i_reval, "revalidate must run after personas are registered"
