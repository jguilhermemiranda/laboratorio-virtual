
import json
import socket
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext

from guy_common import (
    ACCENT_LIGHT, COLOR1, COLOR2, COLOR3, COLOR4, COLOR5, ERR, MUTED, OK,
    TEXT, WARN, apply_ttk_theme, brand_bar, load_logo, load_settings,
    save_setting, window_icon
)


DEFAULT_PORT = 8765
# Maior que o timeout de execução do servidor (30 s), para a resposta de um
# comando longo nunca chegar depois de o cliente desistir.
REQUEST_TIMEOUT = 45
BG = COLOR4
PANEL = COLOR3
PURPLE = COLOR1


class GuyClient:
    def __init__(self, root):
        self.root = root
        self.root.title("GUYLAB")
        self.root.geometry("1150x800")
        self.root.minsize(900, 720)
        self.root.configure(bg=BG)

        self.sock = None
        self.file = None
        self.session = None
        self.user = None
        self.workspace = None
        self.permissions = {}
        self.current_file = None
        self.lock = threading.Lock()

        self.build_login()
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def clear(self):
        for widget in self.root.winfo_children():
            widget.destroy()

    def build_login(self):
        self.clear()

        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True)

        card = tk.Frame(outer, bg=PANEL, padx=45, pady=35)
        card.place(relx=0.5, rely=0.5, anchor="center")

        logo = load_logo(128)

        if logo is not None:
            tk.Label(card, image=logo, bg=PANEL, bd=0).pack(pady=(0, 10))

        tk.Label(
            card, text="GUYLAB", font=("Segoe UI", 30, "bold"),
            fg=ACCENT_LIGHT, bg=PANEL
        ).pack(pady=(0, 5))

        tk.Label(
            card, text="Autenticação centralizada pelo GUY ACCESS",
            font=("Segoe UI", 10), fg=MUTED, bg=PANEL
        ).pack(pady=(0, 25))

        saved = load_settings()
        self.host_entry = self.field(
            card, "IP da Raspberry", str(saved.get("lab_host", "192.168.1.100"))
        )
        self.port_entry = self.field(
            card, "Porta", str(saved.get("lab_port", DEFAULT_PORT))
        )
        self.username_entry = self.field(card, "Usuário", "")
        self.password_entry = self.field(card, "Senha", "", True)

        self.password_entry.bind("<Return>", lambda _e: self.login())

        tk.Button(
            card, text="ENTRAR", command=self.login,
            font=("Segoe UI", 11, "bold"), bg=PURPLE, fg="white",
            activebackground=COLOR2, relief="flat",
            padx=40, pady=10, cursor="hand2"
        ).pack(pady=(10, 0))

        self.login_status = tk.Label(
            card, text="Desconectado", fg=MUTED, bg=PANEL
        )
        self.login_status.pack(pady=(15, 0))

    def field(self, parent, label, default="", secret=False):
        tk.Label(
            parent, text=label, fg=TEXT, bg=PANEL, anchor="w"
        ).pack(fill="x", pady=(8, 4))

        entry = tk.Entry(
            parent, width=35, font=("Consolas", 12),
            bg=BG, fg="white", insertbackground="white",
            relief="flat", show="*" if secret else ""
        )
        entry.pack(ipady=8)
        if default:
            entry.insert(0, default)
        return entry

    def login(self):
        host = self.host_entry.get().strip()
        username = self.username_entry.get().strip()
        password = self.password_entry.get()

        try:
            port = int(self.port_entry.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            messagebox.showerror("GUYLAB", "Porta inválida.")
            return

        if not host or not username or not password:
            messagebox.showerror("GUYLAB", "Preencha todos os campos.")
            return

        self.login_status.config(text="Conectando...", fg=WARN)
        self.root.update_idletasks()

        try:
            self.sock = socket.create_connection((host, port), timeout=5)
            self.sock.settimeout(REQUEST_TIMEOUT)
            self.file = self.sock.makefile("rb")

            response = self.request({
                "action": "authenticate",
                "username": username,
                "password": password
            }, authenticated=False)

            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Login recusado."))

            self.user = response["user"]
            self.workspace = response["workspace"]
            self.permissions = response["permissions"]
            self.session = response["session"]

            save_setting("lab_host", host)
            save_setting("lab_port", port)

            self.build_main()

        except Exception as exc:
            self.disconnect()
            self.login_status.config(text="Falha na conexão", fg=ERR)
            messagebox.showerror("GUYLAB", str(exc))

    def request(self, data, authenticated=True):
        if not self.sock or not self.file:
            raise ConnectionError("Não conectado ao servidor.")

        payload = dict(data)

        if authenticated and self.session:
            payload["session"] = self.session

        try:
            with self.lock:
                self.sock.sendall(
                    (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                )
                line = self.file.readline()
        except OSError as exc:
            # Após um timeout a resposta ainda pode chegar e ser lida como se
            # fosse a do próximo pedido. Por segurança a conexão é encerrada.
            self.disconnect()
            raise ConnectionError(
                "Conexão com o servidor perdida. "
                "Use 'Sair' e entre novamente."
            ) from exc

        if not line:
            self.disconnect()
            raise ConnectionError("O servidor encerrou a conexão.")

        response = json.loads(line.decode("utf-8"))

        if not isinstance(response, dict):
            raise RuntimeError("Resposta inválida do servidor.")

        return response

    def build_main(self):
        self.clear()

        header = brand_bar(
            self.root, "GUYLAB", "Autenticação centralizada pelo GUY ACCESS"
        )
        header.pack(fill="x")

        tk.Button(
            header, text="Sair", command=self.logout,
            bg=COLOR2, fg="white", activebackground=COLOR1,
            activeforeground="white", relief="flat", padx=14, pady=4,
            cursor="hand2"
        ).pack(side="right", padx=(10, 0))

        tk.Label(
            header, text=f"Conectado: {self.user}",
            fg=OK, bg=PANEL
        ).pack(side="right", padx=10)

        self.tabs = ttk.Notebook(self.root)
        self.tabs.pack(fill="both", expand=True, padx=10, pady=10)

        self.dev_tab = tk.Frame(self.tabs, bg=BG)
        self.accounts_tab = tk.Frame(self.tabs, bg=BG)

        self.tabs.add(self.dev_tab, text="Desenvolvimento")
        self.tabs.add(self.accounts_tab, text="GUY ACCESS · Contas")

        self.build_development()
        self.build_accounts()

        footer = tk.Frame(self.root, bg=PANEL)
        footer.pack(fill="x")

        tk.Label(
            footer,
            text=f"Usuário: {self.user}  •  Workspace: {self.workspace}",
            fg=MUTED, bg=PANEL
        ).pack(anchor="w", padx=12, pady=6)

        if not self.permissions.get("manage_users"):
            self.tabs.tab(self.accounts_tab, state="disabled")

        self.refresh_files()

    def build_development(self):
        split = tk.PanedWindow(
            self.dev_tab, orient="horizontal", bg=BG, sashwidth=5
        )
        split.pack(fill="both", expand=True)

        sidebar = tk.Frame(split, bg=PANEL, width=230)
        main = tk.Frame(split, bg=BG)

        split.add(sidebar, minsize=180)
        split.add(main, minsize=500)

        tk.Label(
            sidebar, text="ARQUIVOS", fg=MUTED, bg=PANEL,
            font=("Segoe UI", 10, "bold")
        ).pack(anchor="w", padx=12, pady=10)

        self.file_list = tk.Listbox(
            sidebar, bg=BG, fg=TEXT, selectbackground=COLOR1,
            relief="flat", font=("Consolas", 10)
        )
        self.file_list.pack(fill="both", expand=True, padx=8, pady=5)
        self.file_list.bind("<Double-Button-1>", lambda _e: self.open_file())

        tk.Button(
            sidebar, text="Atualizar", command=self.refresh_files,
            bg=COLOR2, fg="white", relief="flat"
        ).pack(fill="x", padx=8, pady=5)

        toolbar = tk.Frame(main, bg=PANEL)
        toolbar.pack(fill="x")

        self.file_label = tk.Label(
            toolbar, text="Nenhum arquivo aberto",
            fg=TEXT, bg=PANEL
        )
        self.file_label.pack(side="left", padx=10, pady=8)

        tk.Button(
            toolbar, text="Salvar", command=self.save_file,
            bg=PURPLE, fg="white", relief="flat",
            state="normal" if self.permissions.get("write") else "disabled"
        ).pack(side="right", padx=8, pady=5)

        self.editor = scrolledtext.ScrolledText(
            main, bg=COLOR5, fg=TEXT, insertbackground="white",
            selectbackground=COLOR1, font=("Consolas", 11),
            relief="flat", undo=True
        )
        self.editor.pack(fill="both", expand=True, padx=8, pady=8)

        tk.Label(
            main, text="TERMINAL REMOTO",
            fg=MUTED, bg=BG
        ).pack(anchor="w", padx=10)

        self.terminal = scrolledtext.ScrolledText(
            main, height=8, bg=COLOR5, fg=TEXT,
            insertbackground="white", font=("Consolas", 10),
            relief="flat"
        )
        self.terminal.pack(fill="x", padx=8, pady=5)

        self.command_entry = tk.Entry(
            main, bg=PANEL, fg="white", insertbackground="white",
            font=("Consolas", 10), relief="flat",
            state="normal" if self.permissions.get("execute") else "disabled"
        )
        self.command_entry.pack(fill="x", padx=8, pady=(0, 8), ipady=6)
        self.command_entry.bind("<Return>", lambda _e: self.execute())

    def build_accounts(self):
        bar = tk.Frame(self.accounts_tab, bg=BG)
        bar.pack(fill="x", padx=10, pady=10)

        tk.Button(
            bar, text="Criar conta", command=self.create_account,
            bg=PURPLE, fg="white", relief="flat"
        ).pack(side="left", padx=3)

        tk.Button(
            bar, text="Ativar/desativar", command=self.toggle_account,
            bg=COLOR2, fg="white", relief="flat"
        ).pack(side="left", padx=3)

        tk.Button(
            bar, text="Atualizar", command=self.refresh_accounts,
            bg=COLOR2, fg="white", relief="flat"
        ).pack(side="left", padx=3)

        self.account_tree = ttk.Treeview(
            self.accounts_tab,
            columns=("username", "name", "status", "permissions"),
            show="headings"
        )

        for col, title, width in (
            ("username", "Usuário", 150),
            ("name", "Nome", 220),
            ("status", "Estado", 100),
            ("permissions", "Permissões", 420)
        ):
            self.account_tree.heading(col, text=title)
            self.account_tree.column(col, width=width)

        self.account_tree.pack(fill="both", expand=True, padx=10, pady=8)

        tk.Label(
            self.accounts_tab,
            text="As contas são armazenadas no servidor central da Raspberry Pi.",
            bg=BG, fg=MUTED
        ).pack(anchor="w", padx=10, pady=5)

        self.refresh_accounts()

    def refresh_files(self):
        try:
            response = self.request({"action": "list_files"})
            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Falha ao listar arquivos."))

            self.file_list.delete(0, tk.END)
            for item in response.get("files", []):
                self.file_list.insert(tk.END, item["name"])

        except Exception as exc:
            self.write_terminal(f"[ERRO] {exc}")

    def open_file(self):
        selected = self.file_list.curselection()
        if not selected:
            return

        filename = self.file_list.get(selected[0])

        try:
            response = self.request({
                "action": "read_file",
                "filename": filename
            })

            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Falha na leitura."))

            self.current_file = filename
            self.file_label.config(text=filename)
            self.editor.delete("1.0", tk.END)
            self.editor.insert("1.0", response.get("content", ""))

        except Exception as exc:
            messagebox.showerror("GUYLAB", str(exc))

    def save_file(self):
        if not self.current_file:
            messagebox.showinfo("GUYLAB", "Nenhum arquivo aberto.")
            return

        if not self.permissions.get("write"):
            messagebox.showerror("GUYLAB", "Sem permissão de escrita.")
            return

        try:
            response = self.request({
                "action": "write_file",
                "filename": self.current_file,
                "content": self.editor.get("1.0", "end-1c")
            })

            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Falha ao salvar."))

            self.write_terminal(f"[GUYLAB] {self.current_file} salvo.")

        except Exception as exc:
            messagebox.showerror("GUYLAB", str(exc))

    def execute(self):
        command = self.command_entry.get().strip()
        if not command:
            return

        if not self.permissions.get("execute"):
            messagebox.showerror("GUYLAB", "Sem permissão de execução.")
            return

        self.command_entry.delete(0, tk.END)
        self.write_terminal("> " + command)

        try:
            response = self.request({
                "action": "execute",
                "command": command
            })

            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Falha na execução."))

            self.write_terminal(response.get("output", ""))

        except Exception as exc:
            self.write_terminal("[ERRO] " + str(exc))

    def write_terminal(self, text):
        if not hasattr(self, "terminal"):
            return

        self.terminal.insert(tk.END, str(text) + "\n")
        self.terminal.see(tk.END)

    def refresh_accounts(self):
        if not hasattr(self, "account_tree"):
            return

        try:
            response = self.request({"action": "list_users"})

            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Falha ao listar contas."))

            self.account_tree.delete(*self.account_tree.get_children())

            for user in response.get("users", []):
                perms = user["permissions"]
                enabled = ", ".join(
                    key for key, value in perms.items() if value
                ) or "nenhuma"

                self.account_tree.insert(
                    "", "end",
                    values=(
                        user["username"],
                        user["display_name"],
                        "ATIVO" if user["active"] else "DESATIVADO",
                        enabled
                    )
                )

        except Exception as exc:
            self.write_terminal("[CONTAS] " + str(exc))

    def create_account(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Criar conta GUY ACCESS")
        dialog.configure(bg=PANEL)
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        entries = {}

        for key, label, secret in (
            ("username", "Usuário", False),
            ("display_name", "Nome completo", False),
            ("password", "Senha (mínimo 12 caracteres)", True)
        ):
            tk.Label(
                dialog, text=label, bg=PANEL, fg=TEXT
            ).pack(anchor="w", padx=15, pady=(10, 3))

            entry = tk.Entry(
                dialog, width=35, bg=BG, fg="white",
                insertbackground="white", show="*" if secret else ""
            )
            entry.pack(padx=15, ipady=5)
            entries[key] = entry

        options = {}
        for key, label, default in (
            ("read", "Ler arquivos", True),
            ("write", "Editar arquivos", False),
            ("execute", "Executar comandos", False),
            ("manage_users", "Gerenciar contas", False)
        ):
            variable = tk.BooleanVar(value=default)
            tk.Checkbutton(
                dialog, text=label, variable=variable,
                bg=PANEL, fg=TEXT, selectcolor=BG,
                activebackground=PANEL, activeforeground=TEXT
            ).pack(anchor="w", padx=15, pady=2)
            options[key] = variable

        def submit():
            payload = {
                "action": "create_user",
                "username": entries["username"].get().strip(),
                "display_name": entries["display_name"].get().strip(),
                "password": entries["password"].get(),
                "permissions": {
                    key: variable.get() for key, variable in options.items()
                }
            }

            try:
                response = self.request(payload)

                if not response.get("ok"):
                    raise RuntimeError(response.get("error", "Falha ao criar conta."))

                dialog.destroy()
                self.refresh_accounts()
                messagebox.showinfo("GUY ACCESS", "Conta criada com sucesso.")

            except Exception as exc:
                messagebox.showerror("GUY ACCESS", str(exc))

        tk.Button(
            dialog, text="CRIAR CONTA", command=submit,
            bg=PURPLE, fg="white", relief="flat", padx=20, pady=8
        ).pack(pady=15)

    def toggle_account(self):
        selected = self.account_tree.selection()
        if not selected:
            messagebox.showwarning("GUY ACCESS", "Selecione uma conta.")
            return

        values = self.account_tree.item(selected[0], "values")
        username, _name, status, _perms = values
        active = status != "ATIVO"

        if username.casefold() == self.user.casefold():
            messagebox.showwarning(
                "GUY ACCESS", "Não é permitido alterar a própria conta."
            )
            return

        if not messagebox.askyesno(
            "GUY ACCESS",
            f"{'Ativar' if active else 'Desativar'} a conta {username}?"
        ):
            return

        try:
            response = self.request({
                "action": "set_user_active",
                "username": username,
                "active": active
            })

            if not response.get("ok"):
                raise RuntimeError(response.get("error", "Falha ao alterar conta."))

            self.refresh_accounts()

        except Exception as exc:
            messagebox.showerror("GUY ACCESS", str(exc))

    def logout(self):
        if self.sock and self.session:
            try:
                self.request({"action": "disconnect"})
            except Exception:
                pass

        self.disconnect()
        self.current_file = None
        self.build_login()

    def disconnect(self):
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

        self.sock = None
        self.file = None
        self.session = None

    def close(self):
        if self.sock and self.session:
            try:
                self.request({"action": "disconnect"})
            except Exception:
                pass

        self.disconnect()
        self.root.destroy()


def main():
    root = tk.Tk()
    apply_ttk_theme(root)
    window_icon(root)
    GuyClient(root)
    root.mainloop()


if __name__ == "__main__":
    main()
