"""Budget governor — enforce BoundsSpec.caps consumption ceilings (E2-S4)."""

from stackowl.pipeline.budget.callback import make_budget_callback
from stackowl.pipeline.budget.governor import BudgetGovernor

__all__ = ["BudgetGovernor", "make_budget_callback"]
