"""
setup_profile.py — Setup ÚNICO, manual e interativo do perfil Edge dedicado.

Rode UMA VEZ antes do primeiro uso. Este script abre o Edge usando o perfil
isolado configurado em config.json (edge_profile_dir). Você precisa, dentro desse
Edge, fazer DUAS coisas:

  1. Instalar a extensao LingQ Importer
     - Acesse: https://microsoftedge.microsoft.com/addons/  e pesquise "LingQ Importer"
     - OU instale a versao do Chrome Web Store (Edge aceita extensoes do Chrome):
       https://chromewebstore.google.com/  -> pesquise "LingQ Importer"
     - Clique em "Obter" / "Add to Edge".

  2. Fazer login em lingq.com
     - Acesse: https://www.lingq.com/login
     - Faca login com sua conta.
     - A sessao fica persistida no perfil isolado, para os proximos imports
       serem feitos sem precisar logar de novo.

Depois disso, FECHE o browser. O perfil estara pronto para uso automatico
diario via import_videos.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = PROJECT_DIR / "config.json"


def main() -> int:
    if not CONFIG_PATH.exists():
        print("[ERRO] config.json nao encontrado.")
        return 1

    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        config = json.load(f)

    profile_dir = Path(config["edge_profile_dir"])
    profile_dir.mkdir(parents=True, exist_ok=True)
    edge_exe = config.get("edge_executable") or None

    print("=" * 70)
    print("SETUP DO PERFIL EDGE DEDICADO")
    print("=" * 70)
    print(f"Perfil em: {profile_dir}")
    print()
    print("Vou abrir o Edge isolado agora. Dentro dele:")
    print("  1) Instale a extensao LingQ Importer")
    print("     https://chromewebstore.google.com/  (pesquise 'LingQ Importer')")
    print("  2) Faca login em https://www.lingq.com/login")
    print("  3) Quando terminar, FECHE o browser para gravar o perfil.")
    print()
    input("Pressione ENTER para abrir o Edge...")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[ERRO] playwright nao instalado. Rode:")
        print("       pip install -r requirements.txt")
        print("       playwright install msedge")
        return 1

    with sync_playwright() as p:
        launch_kwargs: dict = {
            "user_data_dir": str(profile_dir),
            "channel": "msedge",
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
            ],
        }
        if edge_exe and Path(edge_exe).exists():
            launch_kwargs["executable_path"] = edge_exe

        ctx = p.chromium.launch_persistent_context(**launch_kwargs)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        # Abre direto a pagina de login do LingQ para conveniencia
        page.goto("https://www.lingq.com/login", wait_until="domcontentloaded")
        print()
        print("Edge aberto. Complete as duas etapas (extensao + login), depois feche o browser.")
        print("Aguardando voce fechar a janela...")
        try:
            # Bloqueia ate todas as paginas serem fechadas pelo usuario
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        ctx.close()

    print()
    print("Perfil salvo. Voce ja pode rodar:  python import_videos.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
