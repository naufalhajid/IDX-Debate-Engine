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
    }
)

console = Console(theme=IDX_THEME)
