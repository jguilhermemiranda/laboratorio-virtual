"""Infraestrutura compartilhada do ecossistema G.U.Y.

Regras deste módulo:
- Somente biblioteca padrão: o servidor GUYLAB roda na Raspberry sem dependências.
- tkinter só é importado dentro das funções de interface, nunca no topo,
  para que importar este arquivo funcione em máquinas sem ambiente gráfico.
"""

import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Caminhos (fonte única). Todos relativos à pasta do projeto, nunca ao diretório
# de onde o programa foi iniciado.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent


def _ascii_safe_dir(preferred, name):
    """No Windows o OpenCV não abre arquivos de modelo em caminhos com acento
    (ex.: "autenticação"): o erro aparece como "Can't read ONNX file". Se a
    pasta do projeto tiver caracteres não ASCII, os modelos ficam em uma pasta
    ASCII do usuário (LOCALAPPDATA, depois PROGRAMDATA, depois temporária)."""
    if str(preferred).isascii():
        return preferred

    for base in (
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("PROGRAMDATA"),
        tempfile.gettempdir(),
    ):
        if base and str(base).isascii():
            return Path(base) / "GUY" / name

    return preferred


ASSETS_DIR = ROOT / "assets"
LOG_DIR = ROOT / "logs"
MODEL_DIR = _ascii_safe_dir(ROOT / "models", "models")
SETTINGS_FILE = ROOT / "guy_settings.json"

ACCESS_DB = ROOT / "guy_access.db"      # GUY ACCESS: pessoas, cartões, digitais, auditoria
FACE_DB = ROOT / "guy_face.db"          # Reconhecimento facial: representações e eventos
IDENTITY_DB = ROOT / "guy_identity.db"  # GUYLAB: contas do servidor
WORKSPACES_DIR = ROOT / "workspaces"    # GUYLAB: arquivos por usuário

# ---------------------------------------------------------------------------
# Identidade visual. COLOR1..COLOR5 é a paleta oficial da marca.
# TEXT/MUTED são neutros com leve tom da paleta; ACCENT_LIGHT vem do degradê
# da marca; OK/WARN/ERR são cores semânticas de estado (fora da paleta).
# ---------------------------------------------------------------------------
COLOR1 = "#6a2f68"
COLOR2 = "#50234e"
COLOR3 = "#351834"
COLOR4 = "#1b0c1a"
COLOR5 = "#000000"

TEXT = "#f5eaf5"
MUTED = "#cbb8cb"
ACCENT_LIGHT = "#a3eae7"

OK = "#22c55e"
WARN = "#f59e0b"
ERR = "#ef4444"

LOGO_HEIGHTS = (64, 128, 256)
_logo_cache = {}


def apply_ttk_theme(root):
    """Aplica a paleta G.U.Y. ao tema ttk e à janela raiz."""
    from tkinter import ttk

    style = ttk.Style(root)
    style.theme_use("clam")
    root.configure(bg=COLOR4)

    root.option_add("*TCombobox*Listbox.background", COLOR5)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", COLOR1)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")

    style.configure(
        ".", background=COLOR4, foreground=TEXT, fieldbackground=COLOR5,
        bordercolor=COLOR2, lightcolor=COLOR3, darkcolor=COLOR3,
        troughcolor=COLOR3
    )
    style.configure("TFrame", background=COLOR4)
    style.configure("Panel.TFrame", background=COLOR3)
    style.configure("TLabel", background=COLOR4, foreground=TEXT)
    style.configure("Panel.TLabel", background=COLOR3, foreground=TEXT)
    style.configure("Header.TLabel", background=COLOR3, foreground=TEXT)
    style.configure("Muted.TLabel", background=COLOR4, foreground=MUTED)

    style.configure(
        "TButton", background=COLOR1, foreground="#ffffff", padding=7,
        borderwidth=0, focusthickness=0
    )
    style.map(
        "TButton",
        background=[("disabled", COLOR3), ("active", COLOR2)],
        foreground=[("disabled", MUTED)]
    )

    style.configure("TNotebook", background=COLOR4, borderwidth=0)
    style.configure(
        "TNotebook.Tab", background=COLOR3, foreground="#ffffff",
        padding=(12, 8)
    )
    style.map("TNotebook.Tab", background=[("selected", COLOR1)])

    style.configure(
        "Treeview", background=COLOR5, foreground=TEXT,
        fieldbackground=COLOR5, rowheight=26, borderwidth=0
    )
    style.map(
        "Treeview",
        background=[("selected", COLOR1)],
        foreground=[("selected", "#ffffff")]
    )
    style.configure(
        "Treeview.Heading", background=COLOR2, foreground="#ffffff",
        relief="flat"
    )
    style.map("Treeview.Heading", background=[("active", COLOR1)])

    style.configure(
        "TEntry", fieldbackground=COLOR5, foreground=TEXT, insertcolor=TEXT
    )
    style.configure(
        "TCombobox", fieldbackground=COLOR5, foreground=TEXT,
        background=COLOR1, arrowcolor="#ffffff"
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", COLOR5)],
        foreground=[("readonly", TEXT)]
    )
    style.configure(
        "TScrollbar", background=COLOR1, troughcolor=COLOR3,
        bordercolor=COLOR3, arrowcolor="#ffffff"
    )
    style.configure("TCheckbutton", background=COLOR4, foreground=TEXT)
    style.configure(
        "Horizontal.TProgressbar", background=ACCENT_LIGHT, troughcolor=COLOR3
    )
    return style


def load_logo(height=64):
    """Carrega a marca em uma das alturas pré-renderizadas (64/128/256).

    Retorna None se o arquivo não existir: a interface continua funcionando,
    apenas sem a imagem. O PhotoImage fica guardado em cache porque o Tk
    descarta imagens sem referência.
    """
    import tkinter as tk

    height = min(LOGO_HEIGHTS, key=lambda value: abs(value - height))

    if height not in _logo_cache:
        path = ASSETS_DIR / f"guy_mark_{height}.png"
        try:
            _logo_cache[height] = tk.PhotoImage(file=str(path))
        except (tk.TclError, OSError):
            _logo_cache[height] = None

    return _logo_cache[height]


def window_icon(root):
    """Define a marca como ícone da janela."""
    import tkinter as tk

    image = load_logo(64)

    if image is not None:
        try:
            root.iconphoto(True, image)
        except tk.TclError:
            pass


def brand_bar(parent, title, subtitle=None, logo_height=64):
    """Barra de cabeçalho com a marca. O chamador faz o pack() da barra e
    pode empacotar widgets extras (ex.: status) com side="right"."""
    import tkinter as tk

    bar = tk.Frame(parent, bg=COLOR3, padx=16, pady=10)
    image = load_logo(logo_height)

    if image is not None:
        tk.Label(bar, image=image, bg=COLOR3, bd=0).pack(
            side="left", padx=(0, 14)
        )

    text = tk.Frame(bar, bg=COLOR3)
    text.pack(side="left")

    tk.Label(
        text, text=title, font=("Segoe UI", 20, "bold"),
        fg="#ffffff", bg=COLOR3
    ).pack(anchor="w")

    if subtitle:
        tk.Label(
            text, text=subtitle, font=("Segoe UI", 10),
            fg=MUTED, bg=COLOR3
        ).pack(anchor="w")

    return bar


# ---------------------------------------------------------------------------
# Configurações de conveniência (último IP/porta, última COM). Nunca guardar
# senhas ou credenciais aqui.
# ---------------------------------------------------------------------------
def load_settings():
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    return data if isinstance(data, dict) else {}


def save_setting(key, value):
    """Grava uma configuração de forma atômica. Retorna False se falhar;
    isso é conveniência e nunca deve derrubar o programa."""
    settings = load_settings()
    settings[key] = value
    temporary = SETTINGS_FILE.with_name(SETTINGS_FILE.name + ".tmp")

    try:
        temporary.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        os.replace(temporary, SETTINGS_FILE)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Modelos do reconhecimento facial (YuNet + SFace, OpenCV Zoo).
# ---------------------------------------------------------------------------
FACE_MODELS = {
    "yunet.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/refs/heads/main/"
        "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
    ),
    "sface.onnx": (
        "https://github.com/opencv/opencv_zoo/raw/refs/heads/main/"
        "models/face_recognition_sface/face_recognition_sface_2021dec.onnx"
    ),
}
MIN_MODEL_BYTES = 100_000


def _looks_like_onnx(path):
    """Rejeita o caso clássico de download errado: página HTML de erro ou
    ponteiro do Git LFS salvos com o nome do modelo."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(64).lstrip()
    except OSError:
        return False

    return not head.startswith((b"<", b"version https://git-lfs"))


def _model_ok(path):
    return (
        path.exists()
        and path.stat().st_size > MIN_MODEL_BYTES
        and _looks_like_onnx(path)
    )


def face_models_ready():
    return all(_model_ok(MODEL_DIR / name) for name in FACE_MODELS)


def face_models_loadable():
    """(ok, detalhe): carrega os modelos com o OpenCV de verdade. É o único
    teste que separa "arquivo ruim" de "OpenCV incompatível"."""
    try:
        import cv2
    except ImportError:
        return False, "OpenCV não instalado"

    try:
        cv2.FaceDetectorYN.create(
            str(MODEL_DIR / "yunet.onnx"), "", (320, 320)
        )
        cv2.FaceRecognizerSF.create(str(MODEL_DIR / "sface.onnx"), "")
    except Exception as exc:  # noqa: BLE001 - cv2.error e afins
        return False, f"OpenCV {cv2.__version__} não carregou os modelos: {exc}"

    return True, f"OpenCV {cv2.__version__}"


def download_face_models(log=print):
    """Baixa os modelos que faltam. Levanta exceção em qualquer falha; o
    arquivo só aparece com o nome final depois de baixado por completo."""
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    for filename, url in FACE_MODELS.items():
        path = MODEL_DIR / filename

        if _model_ok(path):
            continue

        log(f"Baixando modelo: {filename}")

        request = urllib.request.Request(
            url, headers={"User-Agent": "GUY-Face/1.0"}
        )

        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()

        if len(data) < MIN_MODEL_BYTES or data.lstrip().startswith(b"<"):
            raise RuntimeError(f"Download inválido do modelo {filename}.")

        partial = path.with_name(path.name + ".part")
        partial.write_bytes(data)
        os.replace(partial, path)


# ---------------------------------------------------------------------------
# Integração GUY ACCESS <-> reconhecimento facial.
# O banco do GUY ACCESS é a fonte administrativa de identidades; o módulo
# facial só lê (modo somente leitura) e guarda representações faciais
# associadas ao ID da pessoa.
# ---------------------------------------------------------------------------
def access_people():
    """Pessoas do GUY ACCESS como [{"id": "1", "name": ..., "active": bool}].

    - [] se o banco ainda não existe ou não há pessoas;
    - None se o banco existe mas não pôde ser lido (o chamador decide o que
      fazer e deve avisar o usuário).
    """
    if not ACCESS_DB.exists():
        return []

    uri = ACCESS_DB.resolve().as_uri() + "?mode=ro"

    try:
        db = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            rows = db.execute(
                "SELECT id, name, active FROM people ORDER BY name"
            ).fetchall()
        finally:
            db.close()
    except sqlite3.Error:
        return None

    return [
        {"id": str(row[0]), "name": row[1], "active": bool(row[2])}
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Execução de módulos como processos separados (isolamento de falhas: uma
# câmera travada não pode derrubar o controle de acesso).
# ---------------------------------------------------------------------------
def spawn_module(script, args=(), log_name=None):
    """Inicia um script do projeto com o mesmo Python, registrando a saída em
    logs/<nome>.log. Retorna (processo, caminho_do_log)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"{log_name or Path(script).stem}.log"

    env = dict(
        os.environ,
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        PYTHONUNBUFFERED="1"
    )

    with open(log_path, "wb") as log:
        process = subprocess.Popen(
            [sys.executable, str(ROOT / script), *[str(a) for a in args]],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )

    return process, log_path


def tail_log(path, lines=15):
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    return "\n".join(text.splitlines()[-lines:])


# ---------------------------------------------------------------------------
# Ambiente e diagnóstico (usado pelo main.py e pelo launcher).
# ---------------------------------------------------------------------------
# módulo importável -> pacote no pip. Mantenha alinhado com requirements.txt.
REQUIRED_IMPORTS = {
    "serial": "pyserial",
    "cv2": "opencv-python",
    "numpy": "numpy",
    "PIL": "Pillow",
}


def in_virtualenv():
    return sys.prefix != getattr(sys, "base_prefix", sys.prefix)


def missing_modules():
    return [
        package for module, package in REQUIRED_IMPORTS.items()
        if importlib.util.find_spec(module) is None
    ]


def serial_ports():
    """Portas seriais visíveis, ou None se o pyserial não está instalado."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return None

    return [port.device for port in list_ports.comports()]


def diagnose():
    """Retorna [(rótulo, estado, detalhe)] com estado em ok | warn | fail.
    Nunca abre câmera nem porta serial (diagnóstico não pode ter efeitos)."""
    checks = []

    where = "ambiente virtual" if in_virtualenv() else "instalação global"
    checks.append(("Python", "ok", f"{sys.version.split()[0]} ({where})"))

    absent = set(missing_modules())

    for module, package in REQUIRED_IMPORTS.items():
        if package in absent:
            checks.append((
                f"Pacote {package}", "fail",
                "ausente — execute: python main.py --reinstall"
            ))
        else:
            checks.append((f"Pacote {package}", "ok", "instalado"))

    relocated = MODEL_DIR != ROOT / "models"
    checks.append((
        "Pasta de modelos", "ok",
        f"{MODEL_DIR}" + (
            " (fora do projeto: o caminho do projeto tem acentos)"
            if relocated else ""
        )
    ))

    if face_models_ready():
        loadable, detail = face_models_loadable()
        checks.append((
            "Modelos faciais", "ok" if loadable else "fail",
            f"prontos ({detail})" if loadable else detail
        ))
    else:
        checks.append((
            "Modelos faciais", "warn",
            "ausentes — precisam de internet na primeira execução"
        ))

    ports = serial_ports()

    if ports is None:
        checks.append(("Portas seriais", "warn", "pyserial indisponível"))
    elif not ports:
        checks.append((
            "Portas seriais", "warn",
            "nenhuma encontrada (ESP32 conectado por USB?)"
        ))
    else:
        checks.append(("Portas seriais", "ok", ", ".join(ports)))

    for label, path in (
        ("Banco GUY ACCESS", ACCESS_DB),
        ("Banco facial", FACE_DB),
        ("Banco GUYLAB", IDENTITY_DB),
    ):
        if path.exists():
            checks.append((label, "ok", path.name))
        else:
            checks.append((label, "ok", "será criado no primeiro uso"))

    logos_ok = all(
        (ASSETS_DIR / f"guy_mark_{height}.png").exists()
        for height in LOGO_HEIGHTS
    )
    checks.append((
        "Marca (assets)", "ok" if logos_ok else "warn",
        "completa" if logos_ok else "arquivos ausentes em assets/"
    ))

    return checks
