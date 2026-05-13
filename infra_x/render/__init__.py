"""Renderers turn the IR into concrete artifacts (HCL today, canvas later)."""

from infra_x.render.hcl import HCLRenderer, render_stack

__all__ = ["HCLRenderer", "render_stack"]
