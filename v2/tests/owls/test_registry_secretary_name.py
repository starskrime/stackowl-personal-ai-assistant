from stackowl.owls.registry import OwlRegistry


def test_secretary_name_present():
    r = OwlRegistry.with_default_secretary()
    assert r.secretary_name() is not None


def test_secretary_name_absent_on_empty_registry():
    assert OwlRegistry().secretary_name() is None
