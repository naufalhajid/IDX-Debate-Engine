from rich.console import Console
from rich.theme import Theme

IDX_THEME = Theme(
    {
        "idx.header": "bold cyan",
        "idx.ok": "bold green",
        "idx.warn": "bold yellow",
        "idx.error": "bold red",
        "idx.muted": "dim",
        "idx.path": "cyan",
        "idx.ticker": "bold magenta",
        "idx.bull": "bold green",
        "idx.bear": "bold red",
        "idx.chartist": "bold blue",
        "idx.scout": "bold cyan",
        "idx.advocate": "bold yellow",
        "idx.cio": "bold white on blue",
        "idx.buy": "bold green",
        "idx.hold": "bold yellow",
        "idx.avoid": "bold red",
        "idx.section": "bold magenta",
        "idx.label": "bold",
        "idx.value": "white",
        "idx.dimmed": "dim white",
        "idx.highlight": "bold cyan",
        "idx.money": "green",
        "idx.risk": "red",
        "idx.confidence": "yellow",
        "brand": "bold cyan",
        "ok": "bold green",
        "warn": "bold yellow",
        "danger": "bold red",
        "muted": "dim white",
        "prompt": "bold white",
        "step": "bold magenta",
        "amber": "yellow",
    }
)

console = Console(theme=IDX_THEME)
