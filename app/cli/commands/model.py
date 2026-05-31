import typer
import re
from pathlib import Path
from rich.console import Console

console = Console()

# Path to the .env file in the root of the project
ENV_PATH = Path(__file__).resolve().parents[3] / ".env"

PROVIDERS = {
    "1": "gemini",
    "2": "anthropic",
    "3": "codex"
}

MODELS = {
    "gemini": {
        "flash": ["gemini-2.5-flash", "gemini-2.0-flash", "gemini-1.5-flash"],
        "pro": ["gemini-3.1-pro-preview", "gemini-1.5-pro", "gemini-exp-1206"]
    },
    "anthropic": {
        "flash": ["claude-3-5-haiku-latest", "claude-3-haiku-20240307"],
        "pro": ["claude-3-5-sonnet-latest", "claude-3-opus-latest"]
    },
    "codex": {
        "flash": ["gpt-4o-mini", "gpt-3.5-turbo"],
        "pro": ["gpt-4o", "gpt-4-turbo", "o1-preview", "o3-mini"]
    }
}

def update_env_file(key: str, value: str):
    """Memperbarui atau menambahkan variabel ke dalam file .env."""
    if not ENV_PATH.exists():
        ENV_PATH.write_text(f"{key}={value}\n", encoding="utf-8")
        return

    content = ENV_PATH.read_text(encoding="utf-8")
    pattern = re.compile(rf"^{key}=.*$", re.MULTILINE)
    
    if pattern.search(content):
        new_content = pattern.sub(f"{key}={value}", content)
    else:
        if not content.endswith("\n"):
            content += "\n"
        new_content = content + f"{key}={value}\n"
        
    ENV_PATH.write_text(new_content, encoding="utf-8")

def get_current_provider() -> str:
    """Membaca provider saat ini dari .env atau default ke gemini."""
    if not ENV_PATH.exists():
        return "gemini"
    
    content = ENV_PATH.read_text(encoding="utf-8")
    match = re.search(r"^DEFAULT_LLM_PROVIDER=(.*)$", content, re.MULTILINE)
    if match:
        return match.group(1).strip()
    return "gemini"

def select_model(provider: str, tier: str) -> str:
    choices = MODELS[provider][tier]
    console.print(f"\nPilih model untuk [bold cyan]{provider.capitalize()} - {tier.capitalize()} Tier[/bold cyan]:")
    for i, m in enumerate(choices, 1):
        console.print(f"  [[cyan]{i}[/cyan]] {m}")
    console.print("  [[cyan]c[/cyan]] Masukkan nama model kustom")
    
    choice = input(f"Pilihan Anda [1-{len(choices)}/c]: ").strip().lower()
    
    if choice == 'c':
        custom_model = input("Masukkan nama model kustom: ").strip()
        return custom_model if custom_model else choices[0]
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(choices):
            return choices[idx]
    except ValueError:
        pass
        
    console.print("[yellow]Pilihan tidak valid, menggunakan default.[/yellow]")
    return choices[0]

def model_command(
    provider: str = typer.Argument(None, help="Nama provider (gemini, anthropic, codex)"),
):
    """Pilih Default Model/Provider LLM dan set spesifik model untuk Flash/Pro."""
    if provider:
        provider = provider.lower()
        if provider not in ["gemini", "anthropic", "codex"]:
            console.print(f"[bold red]❌ Provider '{provider}' tidak valid.[/bold red]")
            raise typer.Exit(1)
            
        update_env_file("DEFAULT_LLM_PROVIDER", provider)
        console.print(f"[bold green]✅ Default provider telah diubah ke: {provider}[/bold green]")
        return

    current = get_current_provider()
    
    console.print("\n[bold]Konfigurasi Model Default LLM (IDX Fundamental)[/bold]")
    console.print("=" * 50)
    console.print(f"Model aktif saat ini: [[bold cyan]{current}[/bold cyan]]\n")
    
    console.print("Pilih provider yang ingin digunakan sebagai default:")
    for num, name in PROVIDERS.items():
        marker = " [bold green]← (Aktif)[/bold green]" if name == current else ""
        console.print(f"  [[cyan]{num}[/cyan]] {name.capitalize()}{marker}")
    
    console.print("  [[cyan]0[/cyan]] Batal / Keluar")
    
    try:
        choice = input("\nMasukkan pilihan Anda [0-3]: ").strip()
    except (KeyboardInterrupt, EOFError):
        console.print("\n[yellow]Dibatalkan.[/yellow]")
        raise typer.Exit()

    if choice == "0" or not choice:
        console.print("[yellow]Dibatalkan.[/yellow]")
        raise typer.Exit()
        
    if choice in PROVIDERS:
        selected_provider = PROVIDERS[choice]
        
        flash_model = select_model(selected_provider, "flash")
        pro_model = select_model(selected_provider, "pro")
        
        update_env_file("DEFAULT_LLM_PROVIDER", selected_provider)
        
        if selected_provider == "gemini":
            update_env_file("GEMINI_FLASH_MODEL", flash_model)
            update_env_file("GEMINI_PRO_MODEL", pro_model)
        elif selected_provider == "anthropic":
            update_env_file("ANTHROPIC_FLASH_MODEL", flash_model)
            update_env_file("ANTHROPIC_PRO_MODEL", pro_model)
        elif selected_provider == "codex":
            update_env_file("CODEX_FLASH_MODEL", flash_model)
            update_env_file("CODEX_PRO_MODEL", pro_model)
            
        console.print(f"\n[bold green]✅ Berhasil! Default konfigurasi diubah:[/bold green]")
        console.print(f"   [cyan]Provider[/cyan]    : {selected_provider.capitalize()}")
        console.print(f"   [cyan]Flash Model[/cyan] : {flash_model}")
        console.print(f"   [magenta]Pro Model[/magenta]   : {pro_model}")
    else:
        console.print(f"\n[bold red]❌ Pilihan '{choice}' tidak valid.[/bold red]")
        raise typer.Exit(1)
