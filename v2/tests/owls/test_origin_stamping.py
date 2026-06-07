from stackowl.owls.registry import OwlRegistry


def test_builtin_personas_are_stamped_builtin():
    reg = OwlRegistry()
    reg.register_builtin_personas()
    for m in reg.all():
        assert m.origin == "builtin", f"{m.name} not stamped builtin"
