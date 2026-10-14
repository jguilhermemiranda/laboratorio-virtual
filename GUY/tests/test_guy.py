"""Testes que rodam em qualquer máquina: sem Tk, sem câmera, sem ESP32.

    python -m unittest discover -s tests -v

O que NÃO é coberto aqui (exige a máquina real): janelas, webcam, porta
serial e firmware.
"""

import json
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import guy_common as gc  # noqa: E402
import server  # noqa: E402


# ----------------------------------------------------------------------------
class LabClient:
    """Cliente mínimo do protocolo JSON-por-linha do GUYLAB."""

    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.file = self.sock.makefile("rb")
        self.session = None

    def call(self, **payload):
        if self.session:
            payload["session"] = self.session

        self.sock.sendall((json.dumps(payload) + "\n").encode())
        return json.loads(self.file.readline())

    def login(self, username, password):
        reply = self.call(action="authenticate", username=username, password=password)
        if reply.get("ok"):
            self.session = reply["session"]
        return reply

    def close(self):
        self.sock.close()


class ServerProtocolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.patches = [
            mock.patch.object(server, "DB_PATH", base / "identity.db"),
            mock.patch.object(server, "WORKSPACES", base / "workspaces"),
            mock.patch.object(server, "ITERATIONS", 1000),  # só para acelerar o teste
        ]
        for patch in cls.patches:
            patch.start()

        server.initialize()
        full = {"read": True, "write": True, "execute": True, "manage_users": True}
        server.create_user("admin", "Administrador", "senha-bem-longa-123", full)
        server.create_user("leitor", "Só leitura", "outra-senha-longa-1", {"read": True})

        cls.server = server.Server(("127.0.0.1", 0), server.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        for patch in cls.patches:
            patch.stop()
        cls.tmp.cleanup()

    def client(self, user="admin", password="senha-bem-longa-123"):
        client = LabClient(self.port)
        self.addCleanup(client.close)
        self.assertTrue(client.login(user, password)["ok"])
        return client

    def test_requires_login(self):
        client = LabClient(self.port)
        self.addCleanup(client.close)
        reply = client.call(action="list_files")
        self.assertFalse(reply["ok"])

    def test_wrong_password_rejected(self):
        client = LabClient(self.port)
        self.addCleanup(client.close)
        self.assertFalse(client.login("admin", "errada-errada-12")["ok"])

    def test_write_read_roundtrip(self):
        client = self.client()
        self.assertTrue(client.call(action="write_file", filename="a.txt", content="olá")["ok"])
        self.assertEqual(client.call(action="read_file", filename="a.txt")["content"], "olá")

    def test_path_traversal_blocked(self):
        client = self.client()
        reply = client.call(action="read_file", filename="../identity.db")
        self.assertFalse(reply["ok"])
        reply = client.call(action="write_file", filename="../../x.txt", content="x")
        self.assertFalse(reply["ok"])

    def test_reader_cannot_write_or_execute(self):
        client = self.client("leitor", "outra-senha-longa-1")
        self.assertFalse(client.call(action="write_file", filename="b.txt", content="x")["ok"])
        self.assertFalse(client.call(action="execute", command="echo oi")["ok"])
        self.assertFalse(client.call(action="list_users")["ok"])

    def test_duplicate_user_gives_clear_error(self):
        # Antes da correção: "Erro interno do servidor."
        client = self.client()
        reply = client.call(
            action="create_user", username="ADMIN", display_name="x",
            password="senha-bem-longa-123", permissions={}
        )
        self.assertFalse(reply["ok"])
        self.assertIn("Já existe", reply["error"])

    def test_short_password_rejected(self):
        client = self.client()
        reply = client.call(
            action="create_user", username="novo", display_name="Novo",
            password="curta", permissions={}
        )
        self.assertFalse(reply["ok"])

    def test_cannot_deactivate_self(self):
        client = self.client()
        reply = client.call(action="set_user_active", username="admin", active=False)
        self.assertFalse(reply["ok"])


# ----------------------------------------------------------------------------
class CommonTests(unittest.TestCase):
    def test_paths_match_server_module(self):
        # server.py é autônomo (deploy na Raspberry): os caminhos têm de coincidir.
        self.assertEqual(server.DB_PATH, gc.IDENTITY_DB)
        self.assertEqual(server.WORKSPACES, gc.WORKSPACES_DIR)

    def test_settings_roundtrip_and_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "s.json"
            with mock.patch.object(gc, "SETTINGS_FILE", target):
                self.assertEqual(gc.load_settings(), {})
                self.assertTrue(gc.save_setting("lab_host", "10.0.0.5"))
                self.assertEqual(gc.load_settings()["lab_host"], "10.0.0.5")
                target.write_text("{corrompido", encoding="utf-8")
                self.assertEqual(gc.load_settings(), {})  # não derruba o app

    def test_access_people_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "access.db"
            with mock.patch.object(gc, "ACCESS_DB", db_path):
                self.assertEqual(gc.access_people(), [])  # banco inexistente

                db = sqlite3.connect(db_path)
                db.execute(
                    "CREATE TABLE people (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "name TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, "
                    "created_at INTEGER NOT NULL)"
                )
                db.execute("INSERT INTO people(name, active, created_at) VALUES ('Ana', 1, 0)")
                db.execute("INSERT INTO people(name, active, created_at) VALUES ('Bruno', 0, 0)")
                db.commit()
                db.close()

                people = gc.access_people()
                self.assertEqual(
                    people,
                    [{"id": "1", "name": "Ana", "active": True},
                     {"id": "2", "name": "Bruno", "active": False}]
                )

                db_path.write_bytes(b"isto nao e um banco sqlite" * 50)
                self.assertIsNone(gc.access_people())  # ilegível -> None, não []

    def test_access_people_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "access.db"
            db = sqlite3.connect(db_path)
            db.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT, active INTEGER)")
            db.commit()
            db.close()
            before = db_path.read_bytes()
            with mock.patch.object(gc, "ACCESS_DB", db_path):
                gc.access_people()
            self.assertEqual(before, db_path.read_bytes())

    def test_diagnose_shape(self):
        for label, state, detail in gc.diagnose():
            self.assertIn(state, ("ok", "warn", "fail"))
            self.assertTrue(label and detail)

    def test_brand_assets_present(self):
        for height in gc.LOGO_HEIGHTS:
            self.assertTrue((gc.ASSETS_DIR / f"guy_mark_{height}.png").exists())


class FaceModelTests(unittest.TestCase):
    def test_accented_project_path_relocates_models(self):
        # Caso real: ...\\autenticação equipe caos\\...\\GUY\\models
        accented = Path("C:/Users/x/Documents/autenticação equipe caos/GUY/models")
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": tmp}):
                chosen = gc._ascii_safe_dir(accented, "models")
        self.assertTrue(str(chosen).isascii())
        self.assertEqual(chosen.name, "models")

    def test_ascii_project_path_is_kept(self):
        plain = Path("C:/GUY/models")
        self.assertEqual(gc._ascii_safe_dir(plain, "models"), plain)

    def test_no_ascii_base_available_keeps_preferred(self):
        accented = Path("/home/joão/GUY/models")
        with mock.patch.dict("os.environ", {"LOCALAPPDATA": "/home/joão/x", "PROGRAMDATA": ""}), \
             mock.patch("tempfile.gettempdir", return_value="/tmp/joão"):
            self.assertEqual(gc._ascii_safe_dir(accented, "models"), accented)

    def test_html_page_saved_as_model_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "yunet.onnx"
            fake.write_bytes(b"<!DOCTYPE html><html>" + b"x" * 200_000)
            self.assertFalse(gc._model_ok(fake))
            lfs = Path(tmp) / "sface.onnx"
            lfs.write_bytes(b"version https://git-lfs.github.com/spec/v1\n" + b"x" * 200_000)
            self.assertFalse(gc._model_ok(lfs))
            real = Path(tmp) / "ok.onnx"
            real.write_bytes(b"\x08\x07" + b"x" * 200_000)
            self.assertTrue(gc._model_ok(real))

    def test_corrupt_model_reports_reason_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in gc.FACE_MODELS:
                (Path(tmp) / name).write_bytes(b"\x00" * 200_000)
            with mock.patch.object(gc, "MODEL_DIR", Path(tmp)):
                ok, detail = gc.face_models_loadable()
        self.assertFalse(ok)
        self.assertIn("OpenCV", detail)


# ----------------------------------------------------------------------------
def import_access_app():
    """Importa app.py com tkinter/serial falsos (só para testar a lógica)."""
    tk = types.ModuleType("tkinter")
    tk.Tk = object
    tk.ttk = mock.MagicMock()
    tk.messagebox = mock.MagicMock()
    serial = types.ModuleType("serial")
    serial.SerialException = Exception
    tools = types.ModuleType("serial.tools")
    list_ports = types.ModuleType("serial.tools.list_ports")
    serial.tools = tools
    tools.list_ports = list_ports
    fakes = {
        "tkinter": tk, "tkinter.ttk": tk.ttk, "tkinter.messagebox": tk.messagebox,
        "serial": serial, "serial.tools": tools, "serial.tools.list_ports": list_ports,
    }
    with mock.patch.dict(sys.modules, fakes):
        sys.modules.pop("app", None)
        import app
    return app


class AccessLogicTests(unittest.TestCase):
    def setUp(self):
        self.app = import_access_app()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app.DB_FILE = str(Path(self.tmp.name) / "access.db")
        self.db = self.app.Database()
        self.ana = self.db.add_person("Ana")
        self.bruno = self.db.add_person("Bruno")
        self.db.add_card("04 AA BB CC", self.ana)       # normaliza para 04AABBCC
        self.db.add_card("04112233", self.bruno)
        self.db.add_fingerprint(1, self.ana)
        self.db.add_fingerprint(2, self.bruno)
        self.sent = []
        now = time.monotonic()
        self.fake = SimpleNamespace(
            db=self.db, pending_cards={}, pending_fingerprints={},
            log=lambda text: None, send=lambda m: self.sent.append(m)
        )
        self.now = now

    def evaluate(self):
        self.app.GuyAccess.evaluate_access(self.fake)

    def decisions(self):
        return [m["allow"] for m in self.sent if m["cmd"] == "ACCESS_DECISION"]

    def test_db_uses_project_path_not_cwd(self):
        self.assertTrue(Path(self.app.ACCESS_DB).is_absolute())

    def test_same_person_card_and_fingerprint_allows(self):
        self.fake.pending_cards["04AABBCC"] = self.now
        self.fake.pending_fingerprints[1] = self.now
        self.evaluate()
        self.assertEqual(self.decisions(), [True])

    def test_different_people_denied(self):
        self.fake.pending_cards["04AABBCC"] = self.now
        self.fake.pending_fingerprints[2] = self.now
        self.evaluate()
        self.assertEqual(self.decisions(), [False])

    def test_unknown_card_denied(self):
        self.fake.pending_cards["DEADBEEF"] = self.now
        self.evaluate()
        self.assertEqual(self.decisions(), [False])

    def test_card_alone_never_allows(self):
        self.fake.pending_cards["04AABBCC"] = self.now
        self.evaluate()
        self.assertEqual(self.decisions(), [])

    def test_inactive_person_denied(self):
        self.db.set_person_active(self.ana, False)
        self.fake.pending_cards["04AABBCC"] = self.now
        self.fake.pending_fingerprints[1] = self.now
        self.evaluate()
        self.assertEqual(self.decisions(), [False])

    def test_expired_pairing_window_does_not_allow(self):
        old = self.now - self.app.PAIR_WINDOW - 5
        self.fake.pending_cards["04AABBCC"] = old
        self.fake.pending_fingerprints[1] = self.now
        self.evaluate()
        self.assertNotIn(True, self.decisions())


# ----------------------------------------------------------------------------
class MainRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import main
        cls.main = main

    def parse(self, *argv):
        with mock.patch.object(sys, "argv", ["main.py", *argv]):
            return self.main.build_parser().parse_known_args()

    def test_server_options_are_forwarded(self):
        args, rest = self.parse("server", "--host", "0.0.0.0", "--port", "9000")
        self.assertEqual(args.command, "server")
        self.assertEqual(rest, ["--host", "0.0.0.0", "--port", "9000"])

    def test_server_create_admin_forwarded(self):
        _args, rest = self.parse("server", "--create-admin")
        self.assertEqual(rest, ["--create-admin"])

    def test_default_is_launcher(self):
        args, rest = self.parse()
        self.assertIsNone(args.command)
        self.assertEqual(rest, [])

    def test_run_module_choices(self):
        args, _ = self.parse("run", "face")
        self.assertEqual(args.module, "face")
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr"):
                self.parse("run", "inexistente")

    def test_reinstall_flag(self):
        args, _ = self.parse("--reinstall")
        self.assertTrue(args.reinstall)

    def test_stamp_changes_with_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            req = Path(tmp) / "requirements.txt"
            with mock.patch.object(self.main, "REQUIREMENTS", req):
                req.write_text("a\n")
                first = self.main.current_stamp()
                req.write_text("a\nb\n")
                self.assertNotEqual(first, self.main.current_stamp())

    def test_module_scripts_exist(self):
        for script, _title in self.main.MODULES.values():
            self.assertTrue((ROOT / script).exists(), script)


if __name__ == "__main__":
    unittest.main()
