"""Janela principal do G.U.Y.

Aberta pelo main.py depois que o ambiente está pronto. Cada módulo roda em
um processo separado: se a webcam travar, o controle de acesso continua
funcionando. O launcher só inicia, acompanha e registra.
"""

import socket
import time
import tkinter as tk
from tkinter import messagebox, ttk

import guy_common as gc
import server  # só biblioteca padrão: porta padrão e criação de administrador

LOCAL_HOST = "127.0.0.1"

# chave, título, descrição, script, argumentos
MODULES = (
    (
        "access", "GUY ACCESS",
        "Pessoas, cartões RFID e digitais. Decide o acesso a partir dos "
        "eventos do ESP32 (USB/serial).",
        "app.py", ()
    ),
    (
        "face", "Reconhecimento facial",
        "Identifica rostos pela webcam. Apenas identifica: não libera "
        "acesso.",
        "guy_face.py", ()
    ),
    (
        "lab", "GUYLAB",
        "Cliente para editar arquivos e executar comandos em um servidor "
        "GUYLAB.",
        "guylab_client.py", ()
    ),
    (
        "server", "Servidor GUYLAB local",
        f"Servidor neste PC, acessível somente por ele "
        f"({LOCAL_HOST}:{server.PORT}). Na Raspberry use: "
        "python3 main.py server",
        "server.py", ("--host", LOCAL_HOST)
    ),
)

STATE_COLORS = {"ok": gc.OK, "warn": gc.WARN, "fail": gc.ERR}


class Launcher(tk.Tk):
    POLL_MS = 500

    def __init__(self):
        super().__init__()

        self.title("G.U.Y.")
        self.geometry("980x880")
        self.minsize(900, 780)

        gc.apply_ttk_theme(self)
        gc.window_icon(self)

        self.processes = {}      # chave -> (Popen, caminho do log)
        self.stopping = set()    # chaves que o próprio launcher está parando
        self.buttons = {}
        self.status_labels = {}

        self.build_interface()
        self.log("Launcher pronto.")
        self.report_problems()

        self.after(self.POLL_MS, self.poll)
        self.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------
    def build_interface(self):
        gc.brand_bar(
            self, "G.U.Y.", "Access · Face · Lab", logo_height=128
        ).pack(fill="x")

        body = tk.Frame(self, bg=gc.COLOR4, padx=12, pady=10)
        body.pack(fill="both", expand=True)

        grid = tk.Frame(body, bg=gc.COLOR4)
        grid.pack(fill="x")
        grid.columnconfigure(0, weight=1, uniform="cards")
        grid.columnconfigure(1, weight=1, uniform="cards")

        for index, spec in enumerate(MODULES):
            self.build_card(grid, index // 2, index % 2, spec)

        self.checks_panel = tk.Frame(
            body, bg=gc.COLOR3, padx=14, pady=10
        )
        self.checks_panel.pack(fill="x", padx=6, pady=6)
        self.render_checks()

        self.console = tk.Text(
            body, height=8, bg=gc.COLOR5, fg=gc.TEXT,
            insertbackground="white", relief="flat", wrap="word"
        )
        self.console.pack(fill="both", expand=True, padx=6, pady=(6, 0))
        self.console.tag_configure("ok", foreground=gc.OK)
        self.console.tag_configure("warn", foreground=gc.WARN)
        self.console.tag_configure("err", foreground=gc.ERR)
        self.console.configure(state="disabled")

    def build_card(self, parent, row, column, spec):
        key, title, description, _script, _args = spec

        card = tk.Frame(parent, bg=gc.COLOR3, padx=14, pady=12)
        card.grid(row=row, column=column, sticky="nsew", padx=6, pady=6)

        tk.Label(
            card, text=title, font=("Segoe UI", 13, "bold"),
            fg="#ffffff", bg=gc.COLOR3
        ).pack(anchor="w")

        tk.Label(
            card, text=description, wraplength=380, justify="left",
            fg=gc.MUTED, bg=gc.COLOR3
        ).pack(anchor="w", pady=(4, 10))

        line = tk.Frame(card, bg=gc.COLOR3)
        line.pack(fill="x")

        button = ttk.Button(
            line, text=self.idle_text(key),
            command=lambda k=key: self.toggle(k)
        )
        button.pack(side="left")
        self.buttons[key] = button

        status = tk.Label(line, text="", fg=gc.MUTED, bg=gc.COLOR3)
        status.pack(side="left", padx=10)
        self.status_labels[key] = status

        if key == "server":
            ttk.Button(
                line, text="Criar administrador",
                command=self.create_admin
            ).pack(side="right")

    def render_checks(self):
        for child in self.checks_panel.winfo_children():
            child.destroy()

        header = tk.Frame(self.checks_panel, bg=gc.COLOR3)
        header.pack(fill="x")

        tk.Label(
            header, text="Ambiente", font=("Segoe UI", 11, "bold"),
            fg="#ffffff", bg=gc.COLOR3
        ).pack(side="left")

        ttk.Button(
            header, text="Reverificar", command=self.render_checks
        ).pack(side="right")

        self.checks = gc.diagnose()

        for label, state, detail in self.checks:
            line = tk.Frame(self.checks_panel, bg=gc.COLOR3)
            line.pack(fill="x")

            tk.Label(
                line, text="●", fg=STATE_COLORS[state], bg=gc.COLOR3
            ).pack(side="left")

            tk.Label(
                line, text=label, width=24, anchor="w",
                fg=gc.TEXT, bg=gc.COLOR3
            ).pack(side="left", padx=(6, 0))

            tk.Label(
                line, text=detail, anchor="w", fg=gc.MUTED, bg=gc.COLOR3
            ).pack(side="left")

    def report_problems(self):
        for label, state, detail in self.checks:
            if state == "fail":
                self.log(f"{label}: {detail}", "err")
            elif state == "warn":
                self.log(f"{label}: {detail}", "warn")

    def log(self, text, tag=None):
        self.console.configure(state="normal")
        self.console.insert(
            "end", f"[{time.strftime('%H:%M:%S')}] {text}\n", tag or ()
        )
        self.console.see("end")
        self.console.configure(state="disabled")

    # ------------------------------------------------------------------
    # Processos
    # ------------------------------------------------------------------
    @staticmethod
    def spec(key):
        return next(item for item in MODULES if item[0] == key)

    @staticmethod
    def idle_text(key):
        return "Iniciar" if key == "server" else "Abrir"

    def running(self, key):
        return key in self.processes

    def toggle(self, key):
        if self.running(key):
            if key == "server":
                self.stop_server()
            return

        self.start(key)

    def start(self, key):
        _key, title, _description, script, args = self.spec(key)

        try:
            process, log_path = gc.spawn_module(script, args, log_name=key)
        except OSError as exc:
            self.log(f"{title}: não foi possível iniciar ({exc}).", "err")
            return

        self.processes[key] = (process, log_path)
        self.log(f"{title} iniciado (PID {process.pid}).", "ok")
        self.refresh_buttons()

        if key == "server":
            self.after(1200, self.check_server_ready)

    def check_server_ready(self):
        if not self.running("server"):
            return  # já terminou; o poll registrou o motivo

        try:
            socket.create_connection((LOCAL_HOST, server.PORT), timeout=1).close()
        except OSError:
            self.log(
                f"Servidor iniciado, mas a porta {server.PORT} ainda não "
                "responde. Veja logs/server.log.", "warn"
            )
            return

        self.log(
            f"Servidor pronto em {LOCAL_HOST}:{server.PORT}. "
            "Abra o GUYLAB e entre com uma conta de administrador.", "ok"
        )

    def stop_server(self):
        entry = self.processes.get("server")

        if entry is None:
            return

        self.stopping.add("server")
        entry[0].terminate()
        self.log("Parando o servidor local...")

    def poll(self):
        for key in list(self.processes):
            process, log_path = self.processes[key]
            code = process.poll()

            if code is None:
                continue

            del self.processes[key]
            title = self.spec(key)[1]

            if key in self.stopping:
                self.stopping.discard(key)
                self.log(f"{title} parado.")
            elif code == 0:
                self.log(f"{title} encerrado.")
            else:
                self.log(
                    f"{title} encerrou com erro (código {code}). "
                    f"Log: logs/{log_path.name}", "err"
                )
                tail = gc.tail_log(log_path)

                if tail:
                    self.log(tail, "err")

        self.refresh_buttons()
        self.after(self.POLL_MS, self.poll)

    def refresh_buttons(self):
        for key, button in self.buttons.items():
            if self.running(key):
                pid = self.processes[key][0].pid

                if key == "server":
                    button.configure(text="Parar", state="normal")
                else:
                    button.configure(text="Em execução", state="disabled")

                self.status_labels[key].configure(
                    text=f"PID {pid}", fg=gc.OK
                )
            else:
                button.configure(text=self.idle_text(key), state="normal")
                self.status_labels[key].configure(text="")

    # ------------------------------------------------------------------
    # Administrador do GUYLAB (conta local, sem passar pela rede)
    # ------------------------------------------------------------------
    def create_admin(self):
        dialog = tk.Toplevel(self)
        dialog.title("Criar administrador do GUYLAB")
        dialog.configure(bg=gc.COLOR3)
        dialog.resizable(False, False)
        dialog.transient(self)

        tk.Label(
            dialog,
            text="Esta conta terá TODAS as permissões, incluindo executar\n"
                 "comandos no computador onde o servidor roda.",
            justify="left", fg=gc.WARN, bg=gc.COLOR3
        ).pack(anchor="w", padx=16, pady=(14, 4))

        entries = {}

        for key, label, secret in (
            ("username", "Usuário (3 a 64: letras, números, . _ -)", False),
            ("display_name", "Nome", False),
            ("password", "Senha (mínimo 12 caracteres)", True),
            ("confirm", "Confirme a senha", True),
        ):
            tk.Label(
                dialog, text=label, fg=gc.TEXT, bg=gc.COLOR3
            ).pack(anchor="w", padx=16, pady=(10, 3))

            entry = tk.Entry(
                dialog, width=38, bg=gc.COLOR5, fg="white",
                insertbackground="white", relief="flat",
                show="*" if secret else ""
            )
            entry.pack(padx=16, ipady=5)
            entries[key] = entry

        def submit():
            password = entries["password"].get()

            if password != entries["confirm"].get():
                messagebox.showerror(
                    "Criar administrador", "As senhas não coincidem.",
                    parent=dialog
                )
                return

            try:
                server.initialize()
                server.create_user(
                    entries["username"].get(),
                    entries["display_name"].get(),
                    password,
                    {
                        "read": True, "write": True,
                        "execute": True, "manage_users": True
                    }
                )
            except ValueError as exc:
                messagebox.showerror(
                    "Criar administrador", str(exc), parent=dialog
                )
                return
            except Exception as exc:  # noqa: BLE001 - banco/disco: mostrar o motivo
                messagebox.showerror(
                    "Criar administrador",
                    f"Falha ao gravar no banco de contas: {exc}",
                    parent=dialog
                )
                return

            dialog.destroy()
            self.log(
                f"Administrador '{entries['username'].get().strip()}' "
                "criado.", "ok"
            )

        tk.Button(
            dialog, text="CRIAR", command=submit, bg=gc.COLOR1, fg="white",
            activebackground=gc.COLOR2, activeforeground="white",
            relief="flat", padx=24, pady=8, cursor="hand2"
        ).pack(pady=16)

        dialog.wait_visibility()
        dialog.grab_set()

    # ------------------------------------------------------------------
    def close(self):
        if self.running("server"):
            if not messagebox.askyesno(
                "G.U.Y.",
                "O servidor GUYLAB local será encerrado. Continuar?"
            ):
                return

            self.processes["server"][0].terminate()

        self.destroy()


def run():
    Launcher().mainloop()
