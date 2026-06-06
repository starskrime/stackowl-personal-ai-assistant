import inspect

from stackowl.startup import orchestrator


def test_orchestrator_invokes_hydrate_dna():
    src = inspect.getsource(orchestrator)
    assert "hydrate_dna" in src  # the boot path hydrates evolved DNA into the registry
