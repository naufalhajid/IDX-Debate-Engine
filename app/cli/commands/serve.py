from __future__ import annotations

from typing import Annotated

import typer

from app.cli.ui.console import console


def run_server(*, host: str, port: int, reload: bool) -> None:
    import uvicorn

    uvicorn.run(
        "app.api.main:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
    )


def serve_command(
    host: Annotated[
        str,
        typer.Option("--host", help="Host interface to bind."),
    ] = "127.0.0.1",
    port: Annotated[
        int,
        typer.Option("--port", "-p", min=1, max=65535, help="Port to bind."),
    ] = 8000,
    no_reload: Annotated[
        bool,
        typer.Option("--no-reload", help="Disable uvicorn development reload."),
    ] = False,
) -> None:
    """Start the FastAPI server."""
    reload = not no_reload
    console.print(
        f"[idx.header]Starting API server[/idx.header] http://{host}:{port} reload={reload}"
    )
    run_server(host=host, port=port, reload=reload)


app = typer.Typer(help="FastAPI server commands.")
app.command(name="serve")(serve_command)


__all__ = ["app", "run_server", "serve_command"]
