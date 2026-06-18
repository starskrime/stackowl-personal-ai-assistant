import inspect
from stackowl.providers import base, anthropic_provider, openai_provider


def test_all_providers_expose_history_param():
    # base.ModelProvider plus the concrete provider classes in each module
    targets = [base.ModelProvider]
    for mod in (anthropic_provider, openai_provider):
        cls = next(
            v for v in vars(mod).values()
            if isinstance(v, type) and hasattr(v, "complete_with_tools")
            and v.__module__ == mod.__name__
        )
        targets.append(cls)
    for cls in targets:
        sig = inspect.signature(cls.complete_with_tools)
        assert "history" in sig.parameters, f"{cls} missing history param"
        assert sig.parameters["history"].default is None
