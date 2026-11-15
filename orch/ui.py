"""Console setup: rich for humans, plain markdown-ish output for agents."""

from __future__ import annotations

import os

from rich import box
from rich.console import Console
from typer_agentic import DEFAULT_AGENT_ENV_VARS

AGENT = any(os.environ.get(v) for v in DEFAULT_AGENT_ENV_VARS)
console = Console(no_color=AGENT, highlight=not AGENT, width=200 if AGENT else None)
err = Console(stderr=True, no_color=AGENT, highlight=not AGENT)
TABLE_BOX = box.MARKDOWN if AGENT else box.ROUNDED
