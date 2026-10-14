
import hashlib
import hmac
import json
import secrets
import socketserver
import sqlite3
import subprocess
import time
from pathlib import Path

HOST = "0.0.0.0"
PORT = 8765
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "guy_identity.db"
WORKSPACES = ROOT / "workspaces"
ITERATIONS = 600_000
MAX_LINE = 2 * 1024 * 1024
MAX_FILE = 1024 * 1024


def db_connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    WORKSPACES.mkdir(parents=True, exist_ok=True)
    with db_connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                can_read INTEGER NOT NULL DEFAULT 1,
                can_write INTEGER NOT NULL DEFAULT 0,
                can_execute INTEGER NOT NULL DEFAULT 0,
                can_manage_users INTEGER NOT NULL DEFAULT 0,
                created_at INTEGER NOT NULL
            )
        """)


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, ITERATIONS
    )


def create_user(username, display_name, password, permissions):
    username = username.strip()
    display_name = display_name.strip()

    if not 3 <= len(username) <= 64:
        raise ValueError("O usuário deve ter de 3 a 64 caracteres.")

    if not all(c.isalnum() or c in "._-" for c in username):
        raise ValueError("Usuário inválido.")

    if not display_name:
        raise ValueError("Informe o nome.")

    if len(password) < 12:
        raise ValueError("A senha deve ter pelo menos 12 caracteres.")

    salt = secrets.token_bytes(16)
    digest = password_hash(password, salt)

    try:
        with db_connect() as db:
            db.execute("""
                INSERT INTO users (
                    username, display_name, salt, password_hash, active,
                    can_read, can_write, can_execute, can_manage_users,
                    created_at
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """, (
                username, display_name, salt, digest,
                int(bool(permissions.get("read", True))),
                int(bool(permissions.get("write", False))),
                int(bool(permissions.get("execute", False))),
                int(bool(permissions.get("manage_users", False))),
                int(time.time())
            ))
    except sqlite3.IntegrityError:
        raise ValueError("Já existe um usuário com esse nome.") from None


def get_user(username):
    with db_connect() as db:
        return db.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username,)
        ).fetchone()


def permissions(user):
    return {
        "read": bool(user["can_read"]),
        "write": bool(user["can_write"]),
        "execute": bool(user["can_execute"]),
        "manage_users": bool(user["can_manage_users"])
    }


def workspace_for(username):
    root = WORKSPACES.resolve()
    path = (WORKSPACES / username).resolve()

    if path.parent != root:
        raise ValueError("Workspace inválido.")

    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_path(workspace, filename):
    if not isinstance(filename, str) or not filename.strip():
        raise ValueError("Nome de arquivo inválido.")

    root = workspace.resolve()
    target = (root / filename).resolve()

    if target != root and root not in target.parents:
        raise ValueError("Caminho fora do workspace.")

    return target


class Handler(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup()
        self.user = None
        self.workspace = None
        self.session = secrets.token_urlsafe(32)

    def send_json(self, value):
        self.wfile.write(
            (json.dumps(value, ensure_ascii=False) + "\n").encode("utf-8")
        )
        self.wfile.flush()

    def receive_json(self):
        line = self.rfile.readline(MAX_LINE + 1)

        if not line:
            return None

        if len(line) > MAX_LINE:
            raise ValueError("Requisição muito grande.")

        value = json.loads(line.decode("utf-8"))

        if not isinstance(value, dict):
            raise ValueError("Formato inválido.")

        return value

    def require(self, permission):
        if not self.user:
            raise PermissionError("Autenticação necessária.")

        if not permissions(self.user).get(permission, False):
            raise PermissionError("Permissão insuficiente.")

    def authenticate(self, request):
        username = request.get("username")
        password = request.get("password")

        if not isinstance(username, str) or not isinstance(password, str):
            raise PermissionError("Usuário ou senha inválidos.")

        user = get_user(username)

        if not user:
            raise PermissionError("Usuário ou senha inválidos.")

        calculated = password_hash(password, user["salt"])

        if not hmac.compare_digest(calculated, user["password_hash"]):
            raise PermissionError("Usuário ou senha inválidos.")

        if not user["active"]:
            raise PermissionError("Conta desativada.")

        self.user = user
        self.workspace = workspace_for(user["username"])

        return {
            "ok": True,
            "user": user["username"],
            "display_name": user["display_name"],
            "workspace": user["username"],
            "permissions": permissions(user),
            "session": self.session
        }

    def dispatch(self, request):
        action = request.get("action")

        if action == "authenticate":
            if self.user:
                raise PermissionError("Conexão já autenticada.")
            return self.authenticate(request)

        if action == "disconnect":
            return {"ok": True}

        if not self.user:
            raise PermissionError("Faça login primeiro.")

        supplied = request.get("session", "")

        if not isinstance(supplied, str) or not hmac.compare_digest(
            supplied, self.session
        ):
            raise PermissionError("Sessão inválida.")

        if action == "list_files":
            self.require("read")
            files = []

            for path in sorted(self.workspace.iterdir()):
                try:
                    if path.is_file() and not path.is_symlink():
                        files.append({
                            "name": path.name,
                            "size": path.stat().st_size
                        })
                except OSError:
                    continue

            return {"ok": True, "files": files}

        if action == "read_file":
            self.require("read")
            path = safe_path(self.workspace, request.get("filename"))

            if not path.is_file() or path.is_symlink():
                raise FileNotFoundError("Arquivo não encontrado.")

            if path.stat().st_size > MAX_FILE:
                raise ValueError("Arquivo muito grande.")

            return {
                "ok": True,
                "content": path.read_text(encoding="utf-8")
            }

        if action == "write_file":
            self.require("write")
            content = request.get("content")

            if not isinstance(content, str):
                raise ValueError("Conteúdo inválido.")

            if len(content.encode("utf-8")) > MAX_FILE:
                raise ValueError("Arquivo muito grande.")

            path = safe_path(self.workspace, request.get("filename"))

            if path.exists() and (path.is_dir() or path.is_symlink()):
                raise ValueError("Destino inválido.")

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return {"ok": True}

        if action == "execute":
            self.require("execute")
            command = request.get("command")

            if not isinstance(command, str) or not command.strip():
                raise ValueError("Comando vazio.")

            if len(command) > 2000:
                raise ValueError("Comando muito longo.")

            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace,
                capture_output=True,
                text=True,
                timeout=30,
                errors="replace"
            )

            output = (result.stdout or "") + (result.stderr or "")

            return {
                "ok": True,
                "output": output[:50000],
                "returncode": result.returncode
            }

        if action == "list_users":
            self.require("manage_users")

            with db_connect() as db:
                rows = db.execute("""
                    SELECT username, display_name, active,
                           can_read, can_write, can_execute, can_manage_users
                    FROM users ORDER BY username
                """).fetchall()

            return {
                "ok": True,
                "users": [
                    {
                        "username": row["username"],
                        "display_name": row["display_name"],
                        "active": bool(row["active"]),
                        "permissions": {
                            "read": bool(row["can_read"]),
                            "write": bool(row["can_write"]),
                            "execute": bool(row["can_execute"]),
                            "manage_users": bool(row["can_manage_users"])
                        }
                    }
                    for row in rows
                ]
            }

        if action == "create_user":
            self.require("manage_users")
            create_user(
                request.get("username", ""),
                request.get("display_name", ""),
                request.get("password", ""),
                request.get("permissions", {})
            )
            return {"ok": True}

        if action == "set_user_active":
            self.require("manage_users")
            username = request.get("username")
            active = request.get("active")

            if not isinstance(username, str) or not isinstance(active, bool):
                raise ValueError("Parâmetros inválidos.")

            if username.casefold() == self.user["username"].casefold():
                raise ValueError("Não altere o estado da própria conta.")

            with db_connect() as db:
                cursor = db.execute(
                    "UPDATE users SET active = ? WHERE username = ? COLLATE NOCASE",
                    (int(active), username)
                )

                if cursor.rowcount == 0:
                    raise ValueError("Usuário não encontrado.")

            return {"ok": True}

        if action == "set_user_permissions":
            self.require("manage_users")
            username = request.get("username")
            supplied = request.get("permissions")

            if not isinstance(username, str) or not isinstance(supplied, dict):
                raise ValueError("Parâmetros inválidos.")

            columns = {
                "read": "can_read",
                "write": "can_write",
                "execute": "can_execute",
                "manage_users": "can_manage_users"
            }

            updates = {}

            for key, value in supplied.items():
                if key not in columns or not isinstance(value, bool):
                    raise ValueError("Permissão inválida.")
                updates[columns[key]] = int(value)

            if not updates:
                raise ValueError("Nenhuma permissão informada.")

            assignments = ", ".join(f"{key} = ?" for key in updates)

            with db_connect() as db:
                cursor = db.execute(
                    f"UPDATE users SET {assignments} WHERE username = ? COLLATE NOCASE",
                    list(updates.values()) + [username]
                )

                if cursor.rowcount == 0:
                    raise ValueError("Usuário não encontrado.")

            return {"ok": True}

        raise ValueError("Ação desconhecida.")

    def handle(self):
        while True:
            try:
                request = self.receive_json()

                if request is None:
                    return

                response = self.dispatch(request)
                self.send_json(response)

                if request.get("action") == "disconnect":
                    return

            except (ValueError, PermissionError, FileNotFoundError) as exc:
                self.send_json({"ok": False, "error": str(exc)})

            except subprocess.TimeoutExpired:
                self.send_json({
                    "ok": False,
                    "error": "O comando excedeu 30 segundos."
                })

            except (BrokenPipeError, ConnectionResetError, OSError):
                return

            except Exception:
                self.send_json({
                    "ok": False,
                    "error": "Erro interno do servidor."
                })


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    import argparse
    import getpass

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--create-admin", action="store_true")
    args = parser.parse_args()

    initialize()

    if args.create_admin:
        username = input("Usuário administrador: ").strip()
        display_name = input("Nome: ").strip()
        password = getpass.getpass("Senha (mínimo 12 caracteres): ")
        confirm = getpass.getpass("Confirme a senha: ")

        if password != confirm:
            raise SystemExit("As senhas não coincidem.")

        create_user(
            username,
            display_name,
            password,
            {
                "read": True,
                "write": True,
                "execute": True,
                "manage_users": True
            }
        )
        print("Administrador criado.")
        return

    with Server((args.host, args.port), Handler) as server:
        print(f"GUYLAB ouvindo em {args.host}:{args.port}")

        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Servidor encerrado.")


if __name__ == "__main__":
    main()
