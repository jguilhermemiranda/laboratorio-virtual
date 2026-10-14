#!/usr/bin/env python3
"""G.U.Y. — ponto de entrada único.

    python main.py                       abre o launcher (na primeira vez
                                         instala tudo o que faltar)
    python main.py check                 diagnostica o ambiente, sem janelas
    python main.py run access|face|lab   abre um módulo direto
    python main.py server [opções]       servidor GUYLAB (só biblioteca
                                         padrão; é o modo da Raspberry Pi)
    python main.py --reinstall           força reinstalar as dependências

Este arquivo usa somente a biblioteca padrão até o ambiente estar pronto.
"""

import argparse
import hashlib
import os
import subprocess
import sys
import venv
from pathlib import Path

import guy_common as gc

ROOT = Path(__file__).resolve().parent
MIN_PYTHON = (3, 9)
REQUIREMENTS = ROOT / "requirements.txt"
VENV_DIR = ROOT / ".venv"

# comando -> (script, nome amigável)
MODULES = {
    "access": ("app.py", "GUY ACCESS"),
    "face": ("guy_face.py", "Reconhecimento facial"),
    "lab": ("guylab_client.py", "GUYLAB (cliente)"),
}


def info(message):
    print(f"[GUY] {message}", flush=True)


def warn(message):
    print(f"[GUY][AVISO] {message}", flush=True)


def fail(message, code=2):
    print(f"\n[GUY][ERRO] {message}", file=sys.stderr, flush=True)
    sys.exit(code)


# ---------------------------------------------------------------------------
# Pré-requisitos do interpretador
# ---------------------------------------------------------------------------
def check_python():
    if sys.version_info < MIN_PYTHON:
        fail(
            f"É necessário Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} ou mais "
            f"novo (encontrado {sys.version.split()[0]})."
        )


def check_tkinter():
    try:
        import tkinter  # noqa: F401
    except ImportError:
        fail(
            "O Tkinter não está disponível neste Python.\n"
            "  Windows: reinstale pelo instalador oficial (python.org) "
            "mantendo 'tcl/tk and IDLE' marcado.\n"
            "  Linux:   sudo apt install python3-tk"
        )


# ---------------------------------------------------------------------------
# Ambiente virtual e dependências
# ---------------------------------------------------------------------------
def venv_python():
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def create_venv_if_needed():
    python = venv_python()

    if python.exists():
        working = subprocess.call(
            [str(python), "-c", "pass"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        ) == 0

        if working:
            return

        fail(
            f"O ambiente virtual em {VENV_DIR} está quebrado (o Python "
            "base mudou ou foi removido).\n"
            "  Apague a pasta .venv e execute novamente; ela será recriada."
        )

    info("Criando o ambiente virtual (.venv). Só acontece na primeira vez...")

    try:
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
    except Exception as exc:  # noqa: BLE001 - qualquer falha precisa de orientação
        fail(
            f"Não foi possível criar o ambiente virtual: {exc}\n"
            "  Linux: sudo apt install python3-venv\n"
            "  Depois apague a pasta .venv (se existir) e tente de novo."
        )


def stamp_file():
    return Path(sys.prefix) / ".guy-deps.sha256"


def current_stamp():
    digest = hashlib.sha256()
    digest.update(REQUIREMENTS.read_bytes())
    digest.update(sys.version.encode("utf-8"))
    return digest.hexdigest()


def install_dependencies_if_needed(force=False):
    if not REQUIREMENTS.exists():
        fail(f"Arquivo {REQUIREMENTS.name} não encontrado em {ROOT}.")

    stamp = stamp_file()

    try:
        stamp_ok = stamp.read_text(encoding="utf-8").strip() == current_stamp()
    except OSError:
        stamp_ok = False

    if not force and stamp_ok and not gc.missing_modules():
        return

    info("Instalando dependências (requirements.txt). Pode levar alguns minutos...")

    code = subprocess.call([
        sys.executable, "-m", "pip", "install",
        "--disable-pip-version-check", "-r", str(REQUIREMENTS)
    ])

    if code != 0:
        fail(
            f"A instalação das dependências falhou (pip retornou {code}).\n"
            "  - Sem internet ou atrás de proxy? O pip precisa acessar pypi.org.\n"
            "  - Para tentar manualmente:\n"
            f"      \"{sys.executable}\" -m pip install -r requirements.txt\n"
            "  Resolva e execute novamente: nada foi iniciado.",
            code=3
        )

    imports = ", ".join(gc.REQUIRED_IMPORTS)
    verify = subprocess.run(
        [sys.executable, "-c", f"import {imports}"],
        capture_output=True, text=True
    )

    if verify.returncode != 0:
        fail(
            "As dependências foram instaladas, mas não carregam:\n"
            + verify.stderr.strip(),
            code=3
        )

    try:
        stamp.write_text(current_stamp(), encoding="utf-8")
    except OSError:
        warn("Não foi possível gravar o marcador de instalação; "
             "a verificação será refeita na próxima execução.")

    info("Dependências prontas.")


def ensure_environment(force=False):
    """Garante venv + dependências. Se ainda não estiver no ambiente virtual,
    reexecuta o main.py dentro dele e NÃO retorna."""
    if not gc.in_virtualenv():
        if os.environ.get("GUY_BOOTSTRAPPED"):
            fail(
                "Não foi possível entrar no ambiente virtual (.venv).\n"
                "  Apague a pasta .venv e execute novamente."
            )

        create_venv_if_needed()
        info("Reiniciando dentro do ambiente virtual (.venv)...")

        environment = dict(os.environ, GUY_BOOTSTRAPPED="1")
        sys.exit(subprocess.call(
            [str(venv_python()), str(Path(__file__).resolve()), *sys.argv[1:]],
            env=environment
        ))

    install_dependencies_if_needed(force)


def ensure_face_models():
    """Melhor esforço: o reconhecimento facial é opcional e a falta dos
    modelos nunca deve impedir o GUY ACCESS ou o GUYLAB."""
    if gc.face_models_ready():
        return True

    info("Baixando os modelos do reconhecimento facial (só na primeira vez)...")

    try:
        gc.download_face_models(log=info)
    except Exception as exc:  # noqa: BLE001
        warn(
            f"Modelos faciais não baixados ({exc}).\n"
            "  O reconhecimento facial fica indisponível até uma execução "
            "com internet; GUY ACCESS e GUYLAB não são afetados."
        )
        return False

    return True


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------
def command_check():
    symbols = {"ok": "[ OK ]", "warn": "[ !! ]", "fail": "[FALHA]"}
    worst = "ok"

    for label, state, detail in gc.diagnose():
        print(f"{symbols[state]} {label:<24} {detail}")

        if state == "fail":
            worst = "fail"

    return 1 if worst == "fail" else 0


def command_server(arguments):
    import server

    if "--create-admin" not in arguments:
        warn(
            "A conexão do GUYLAB NÃO é criptografada (senha em texto puro na "
            "rede) e usuários com permissão 'execute' rodam comandos neste "
            "computador. Use somente em rede confiável."
        )

    sys.argv = ["server.py", *arguments]
    server.main()
    return 0


def command_run(module):
    script, _title = MODULES[module]
    return subprocess.call([sys.executable, str(ROOT / script)], cwd=ROOT)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="main.py", allow_abbrev=False,
        description="G.U.Y. — launcher, instalação automática e servidor."
    )
    parser.add_argument(
        "--reinstall", action="store_true",
        help="força a reinstalação das dependências"
    )

    sub = parser.add_subparsers(dest="command")
    sub.add_parser("check", help="diagnostica o ambiente sem abrir janelas")

    run = sub.add_parser("run", help="abre um módulo diretamente")
    run.add_argument("module", choices=sorted(MODULES))

    # Sem ajuda própria: --help e demais opções são repassados ao servidor.
    sub.add_parser(
        "server", add_help=False, allow_abbrev=False,
        help="servidor GUYLAB (sem dependências; para a Raspberry). "
             "Aceita --host, --port e --create-admin"
    )
    return parser


def main():
    check_python()

    parser = build_parser()
    args, rest = parser.parse_known_args()

    if args.command == "server":
        return command_server(rest)

    if rest:
        parser.error(f"argumentos desconhecidos: {' '.join(rest)}")

    check_tkinter()
    ensure_environment(force=args.reinstall)

    needs_models = args.command in (None, "check") or (
        args.command == "run" and args.module == "face"
    )

    if needs_models:
        ensure_face_models()

    if args.command == "check":
        return command_check()

    if args.command == "run":
        return command_run(args.module)

    import guy_launcher
    guy_launcher.run()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
