
import json
import queue
import sqlite3
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import serial
from serial.tools import list_ports

DB_FILE = "guy_access.db"
PAIR_WINDOW = 15
BAUD_RATE = 115200


class Database:
    def __init__(self):
        self.initialize()

    def connect(self):
        db = sqlite3.connect(DB_FILE, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def initialize(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS people (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cards (
                    uid TEXT PRIMARY KEY,
                    person_id INTEGER NOT NULL REFERENCES people(id),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS fingerprints (
                    slot INTEGER PRIMARY KEY,
                    person_id INTEGER NOT NULL REFERENCES people(id),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp INTEGER NOT NULL,
                    person_id INTEGER,
                    uid TEXT,
                    fp_slot INTEGER,
                    decision TEXT NOT NULL,
                    detail TEXT NOT NULL
                );
            """)

    def add_person(self, name):
        with self.connect() as db:
            cur = db.execute(
                "INSERT INTO people(name, created_at) VALUES (?, ?)",
                (name.strip(), int(time.time()))
            )
            return cur.lastrowid

    def people(self):
        with self.connect() as db:
            return db.execute(
                "SELECT id, name, active FROM people ORDER BY name"
            ).fetchall()

    def person(self, person_id):
        with self.connect() as db:
            return db.execute(
                "SELECT * FROM people WHERE id = ?", (person_id,)
            ).fetchone()

    def set_person_active(self, person_id, active):
        with self.connect() as db:
            db.execute(
                "UPDATE people SET active = ? WHERE id = ?",
                (int(active), person_id)
            )

    def add_card(self, uid, person_id):
        uid = normalize_uid(uid)
        if not uid:
            raise ValueError("UID inválido.")

        with self.connect() as db:
            db.execute("""
                INSERT INTO cards(uid, person_id, active, created_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    person_id = excluded.person_id,
                    active = 1,
                    created_at = excluded.created_at
            """, (uid, person_id, int(time.time())))

    def cards(self):
        with self.connect() as db:
            return db.execute("""
                SELECT c.uid, c.person_id, c.active, p.name
                FROM cards c JOIN people p ON p.id = c.person_id
                ORDER BY p.name
            """).fetchall()

    def card(self, uid):
        with self.connect() as db:
            return db.execute("""
                SELECT c.uid, c.person_id, c.active AS card_active,
                       p.name, p.active AS person_active
                FROM cards c JOIN people p ON p.id = c.person_id
                WHERE c.uid = ?
            """, (normalize_uid(uid),)).fetchone()

    def set_card_active(self, uid, active):
        with self.connect() as db:
            db.execute(
                "UPDATE cards SET active = ? WHERE uid = ?",
                (int(active), normalize_uid(uid))
            )

    def add_fingerprint(self, slot, person_id):
        with self.connect() as db:
            db.execute("""
                INSERT INTO fingerprints(slot, person_id, active, created_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(slot) DO UPDATE SET
                    person_id = excluded.person_id,
                    active = 1,
                    created_at = excluded.created_at
            """, (slot, person_id, int(time.time())))

    def fingerprints(self):
        with self.connect() as db:
            return db.execute("""
                SELECT f.slot, f.person_id, f.active, p.name
                FROM fingerprints f JOIN people p ON p.id = f.person_id
                ORDER BY f.slot
            """).fetchall()

    def fingerprint(self, slot):
        with self.connect() as db:
            return db.execute("""
                SELECT f.slot, f.person_id, f.active AS fp_active,
                       p.name, p.active AS person_active
                FROM fingerprints f JOIN people p ON p.id = f.person_id
                WHERE f.slot = ?
            """, (slot,)).fetchone()

    def set_fingerprint_active(self, slot, active):
        with self.connect() as db:
            db.execute(
                "UPDATE fingerprints SET active = ? WHERE slot = ?",
                (int(active), slot)
            )

    def audit(self, person_id, uid, slot, decision, detail):
        with self.connect() as db:
            db.execute("""
                INSERT INTO audit(
                    timestamp, person_id, uid, fp_slot, decision, detail
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                int(time.time()), person_id, uid, slot, decision, detail
            ))

    def events(self, limit=500):
        with self.connect() as db:
            return db.execute("""
                SELECT a.timestamp, COALESCE(p.name, 'Desconhecido') AS name,
                       a.uid, a.fp_slot, a.decision, a.detail
                FROM audit a
                LEFT JOIN people p ON p.id = a.person_id
                ORDER BY a.id DESC LIMIT ?
            """, (limit,)).fetchall()


def normalize_uid(value):
    return "".join(c for c in str(value).upper() if c in "0123456789ABCDEF")


class SerialBridge:
    def __init__(self, event_queue):
        self.events = event_queue
        self.connection = None
        self.lock = threading.Lock()
        self.reader = None
        self.running = False

    def ports(self):
        return [port.device for port in list_ports.comports()]

    def connect(self, port):
        self.disconnect()
        connection = serial.Serial(
            port, BAUD_RATE, timeout=0.2, write_timeout=1
        )
        with self.lock:
            self.connection = connection
            self.running = True

        self.reader = threading.Thread(target=self.read_loop, daemon=True)
        self.reader.start()
        self.send({"cmd": "PING"})

    def disconnect(self):
        with self.lock:
            self.running = False
            connection = self.connection
            self.connection = None

        if connection:
            try:
                connection.close()
            except serial.SerialException:
                pass

    def send(self, payload):
        raw = (
            json.dumps(payload, separators=(",", ":")) + "\n"
        ).encode("utf-8")

        with self.lock:
            if not self.connection or not self.connection.is_open:
                raise RuntimeError("ESP32 não está conectado.")
            self.connection.write(raw)

    def read_loop(self):
        while True:
            with self.lock:
                if not self.running or not self.connection:
                    return
                connection = self.connection

            try:
                raw = connection.readline()
                if not raw:
                    continue

                if len(raw) > 2048:
                    continue

                try:
                    message = json.loads(raw.decode("utf-8").strip())
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue

                if isinstance(message, dict):
                    if isinstance(message.get("event"), str):
                        self.events.put(message)

            except (serial.SerialException, OSError) as exc:
                self.events.put({
                    "event": "CONNECTION_ERROR",
                    "detail": str(exc)
                })
                with self.lock:
                    self.running = False
                return


class GuyAccess(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("GUY ACCESS")
        self.geometry("1050x720")
        self.minsize(850, 600)
        self.configure(bg="#1b0c1a")

        self.db = Database()
        self.events = queue.Queue()
        self.serial = SerialBridge(self.events)

        self.selected_person_id = None
        self.enrolling_card_for = None
        self.enrolling_fingerprint_for = None
        self.pending_cards = {}
        self.pending_fingerprints = {}

        self.configure_style()
        self.build_interface()
        self.refresh_all()
        self.after(100, self.process_events)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def configure_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(
            "TFrame", background="#1b0c1a"
        )
        style.configure(
            "TLabel", background="#1b0c1a", foreground="#f5eaf5"
        )
        style.configure(
            "TButton", background="#6a2f68", foreground="white", padding=7
        )
        style.map(
            "TButton", background=[("active", "#50234e")]
        )
        style.configure(
            "TNotebook", background="#1b0c1a", borderwidth=0
        )
        style.configure(
            "TNotebook.Tab", background="#351834",
            foreground="white", padding=(12, 8)
        )
        style.map(
            "TNotebook.Tab", background=[("selected", "#6a2f68")]
        )
        style.configure(
            "Treeview", background="#241423", foreground="white",
            fieldbackground="#241423", rowheight=26
        )
        style.configure(
            "Treeview.Heading", background="#50234e", foreground="white"
        )

    def build_interface(self):
        header = ttk.Frame(self, padding=12)
        header.pack(fill="x")

        ttk.Label(
            header, text="GUY ACCESS", font=("Segoe UI", 20, "bold")
        ).pack(side="left")

        self.status = ttk.Label(header, text="Desconectado")
        self.status.pack(side="right")

        connection = ttk.Frame(self, padding=(12, 0, 12, 10))
        connection.pack(fill="x")

        self.port_var = tk.StringVar()
        self.port_box = ttk.Combobox(
            connection, textvariable=self.port_var,
            width=14, state="readonly"
        )
        self.port_box.pack(side="left", padx=4)

        ttk.Button(
            connection, text="Atualizar COM",
            command=self.refresh_ports
        ).pack(side="left", padx=4)

        ttk.Button(
            connection, text="Conectar",
            command=self.connect_serial
        ).pack(side="left", padx=4)

        ttk.Button(
            connection, text="Desconectar",
            command=self.disconnect_serial
        ).pack(side="left", padx=4)

        ttk.Label(
            connection, text="Política: cartão + digital"
        ).pack(side="right")

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=12, pady=8)

        self.people_tab = ttk.Frame(self.tabs, padding=10)
        self.cards_tab = ttk.Frame(self.tabs, padding=10)
        self.fp_tab = ttk.Frame(self.tabs, padding=10)
        self.audit_tab = ttk.Frame(self.tabs, padding=10)

        self.tabs.add(self.people_tab, text="Pessoas")
        self.tabs.add(self.cards_tab, text="Cartões")
        self.tabs.add(self.fp_tab, text="Biometria")
        self.tabs.add(self.audit_tab, text="Auditoria")

        self.build_people_tab()
        self.build_cards_tab()
        self.build_fp_tab()
        self.build_audit_tab()

        self.console = tk.Text(
            self, height=6, bg="#100810", fg="#eadbea",
            insertbackground="white", relief="flat"
        )
        self.console.pack(fill="x", padx=12, pady=(0, 12))
        self.console.insert("end", "Sistema iniciado.\n")
        self.console.configure(state="disabled")

        self.refresh_ports()

    def make_tree(self, parent, columns, headings, widths):
        tree = ttk.Treeview(
            parent, columns=columns, show="headings",
            selectmode="browse"
        )
        for col, heading, width in zip(columns, headings, widths):
            tree.heading(col, text=heading)
            tree.column(col, width=width, anchor="w")
        tree.pack(fill="both", expand=True, pady=8)
        return tree

    def build_people_tab(self):
        bar = ttk.Frame(self.people_tab)
        bar.pack(fill="x")

        self.name_var = tk.StringVar()
        ttk.Label(bar, text="Nome:").pack(side="left")
        ttk.Entry(
            bar, textvariable=self.name_var, width=30
        ).pack(side="left", padx=6)

        ttk.Button(
            bar, text="Cadastrar",
            command=self.add_person
        ).pack(side="left", padx=4)

        ttk.Button(
            bar, text="Ativar/desativar",
            command=self.toggle_person
        ).pack(side="left", padx=4)

        self.people_tree = self.make_tree(
            self.people_tab, ("id", "name", "status"),
            ("ID", "Nome", "Estado"), (70, 500, 120)
        )
        self.people_tree.bind(
            "<<TreeviewSelect>>", self.on_person_selected
        )

    def build_cards_tab(self):
        bar = ttk.Frame(self.cards_tab)
        bar.pack(fill="x")

        ttk.Button(
            bar, text="Vincular cartão à pessoa selecionada",
            command=self.enroll_card
        ).pack(side="left", padx=4)

        ttk.Button(
            bar, text="Bloquear/reativar",
            command=self.toggle_card
        ).pack(side="left", padx=4)

        self.cards_tree = self.make_tree(
            self.cards_tab, ("uid", "name", "status"),
            ("UID", "Pessoa", "Estado"), (240, 400, 120)
        )

    def build_fp_tab(self):
        bar = ttk.Frame(self.fp_tab)
        bar.pack(fill="x")

        ttk.Label(bar, text="Slot 1–127:").pack(side="left")
        self.slot_var = tk.StringVar(value="1")
        ttk.Entry(
            bar, textvariable=self.slot_var, width=8
        ).pack(side="left", padx=6)

        ttk.Button(
            bar, text="Cadastrar digital",
            command=self.enroll_fingerprint
        ).pack(side="left", padx=4)

        ttk.Button(
            bar, text="Bloquear/reativar",
            command=self.toggle_fingerprint
        ).pack(side="left", padx=4)

        self.fp_tree = self.make_tree(
            self.fp_tab, ("slot", "name", "status"),
            ("Slot", "Pessoa", "Estado"), (100, 400, 120)
        )

        ttk.Label(
            self.fp_tab,
            text="O modelo biométrico fica no sensor; o banco guarda o vínculo."
        ).pack(anchor="w")

    def build_audit_tab(self):
        ttk.Button(
            self.audit_tab, text="Atualizar",
            command=self.refresh_audit
        ).pack(anchor="w")

        self.audit_tree = self.make_tree(
            self.audit_tab,
            ("time", "name", "uid", "slot", "decision", "detail"),
            ("Data/hora", "Pessoa", "UID", "Slot", "Decisão", "Detalhe"),
            (150, 150, 130, 60, 100, 330)
        )

    def log(self, text):
        self.console.configure(state="normal")
        self.console.insert(
            "end", f"[{time.strftime('%H:%M:%S')}] {text}\n"
        )
        self.console.see("end")
        self.console.configure(state="disabled")

    def refresh_ports(self):
        ports = self.serial.ports()
        self.port_box["values"] = ports
        if ports and self.port_var.get() not in ports:
            self.port_var.set(ports[0])

    def connect_serial(self):
        port = self.port_var.get()
        if not port:
            messagebox.showwarning("GUY ACCESS", "Nenhuma porta COM encontrada.")
            return
        try:
            self.serial.connect(port)
            self.status.configure(text=f"Conectando: {port}")
            self.log(f"Conexão iniciada em {port}.")
        except Exception as exc:
            messagebox.showerror("Erro serial", str(exc))

    def disconnect_serial(self):
        self.serial.disconnect()
        self.status.configure(text="Desconectado")
        self.log("Conexão serial encerrada.")

    def send(self, message):
        try:
            self.serial.send(message)
            return True
        except Exception as exc:
            messagebox.showerror("Comunicação serial", str(exc))
            return False

    def selected_person(self):
        selected = self.people_tree.selection()
        if not selected:
            messagebox.showwarning(
                "Cadastro", "Selecione uma pessoa primeiro."
            )
            return None
        return int(self.people_tree.item(selected[0], "values")[0])

    def add_person(self):
        name = self.name_var.get().strip()
        if not name:
            messagebox.showwarning("Cadastro", "Informe o nome.")
            return
        person_id = self.db.add_person(name)
        self.name_var.set("")
        self.db.audit(
            person_id, None, None, "ADMIN", "Pessoa cadastrada"
        )
        self.log(f"Pessoa cadastrada: {name} (ID {person_id}).")
        self.refresh_all()

    def on_person_selected(self, _event=None):
        selected = self.people_tree.selection()
        if selected:
            self.selected_person_id = int(
                self.people_tree.item(selected[0], "values")[0]
            )

    def toggle_person(self):
        person_id = self.selected_person()
        if person_id is None:
            return
        person = self.db.person(person_id)
        active = not bool(person["active"])
        self.db.set_person_active(person_id, active)
        self.db.audit(
            person_id, None, None, "ADMIN",
            "Pessoa " + ("ativada" if active else "desativada")
        )
        self.refresh_all()

    def enroll_card(self):
        person_id = self.selected_person()
        if person_id is None:
            return
        self.enrolling_card_for = person_id
        if self.send({"cmd": "ENROLL_CARD"}):
            self.status.configure(text="Aproxime o cartão")
            self.log("Aproxime o cartão para vinculá-lo à pessoa selecionada.")

    def toggle_card(self):
        selected = self.cards_tree.selection()
        if not selected:
            messagebox.showwarning("Cartões", "Selecione um cartão.")
            return
        uid, _name, status = self.cards_tree.item(selected[0], "values")
        active = status != "ATIVO"
        self.db.set_card_active(uid, active)
        self.db.audit(
            None, uid, None, "ADMIN",
            "Cartão " + ("reativado" if active else "bloqueado")
        )
        self.log(f"Cartão {uid}: {'ativo' if active else 'bloqueado'}.")
        self.refresh_all()

    def enroll_fingerprint(self):
        person_id = self.selected_person()
        if person_id is None:
            return
        try:
            slot = int(self.slot_var.get())
            if not 1 <= slot <= 127:
                raise ValueError
        except ValueError:
            messagebox.showwarning(
                "Biometria", "O slot precisa estar entre 1 e 127."
            )
            return

        existing = next(
            (f for f in self.db.fingerprints()
             if f["slot"] == slot and f["active"]),
            None
        )
        if existing and not messagebox.askyesno(
            "Substituir digital",
            "Esse slot está ativo. Continuar pode substituir a digital no sensor."
        ):
            return

        self.enrolling_fingerprint_for = (person_id, slot)
        if self.send({"cmd": "ENROLL_FP", "slot": slot}):
            self.status.configure(text=f"Cadastrando digital: slot {slot}")
            self.log(
                f"Cadastro no slot {slot}. Siga as instruções no console."
            )

    def toggle_fingerprint(self):
        selected = self.fp_tree.selection()
        if not selected:
            messagebox.showwarning("Biometria", "Selecione um slot.")
            return
        slot, _name, status = self.fp_tree.item(selected[0], "values")
        slot = int(slot)
        active = status != "ATIVO"
        self.db.set_fingerprint_active(slot, active)
        self.db.audit(
            None, None, slot, "ADMIN",
            "Digital " + ("reativada" if active else "bloqueada")
        )
        self.refresh_all()

    def handle_event(self, msg):
        event = msg.get("event")

        if event == "READY":
            self.status.configure(text="ESP32 conectado")
            self.log("ESP32 pronto.")

        elif event == "CARD_ENROLLED":
            uid = normalize_uid(msg.get("uid", ""))
            if self.enrolling_card_for is None or not uid:
                self.log(f"Cartão detectado fora do cadastro: {uid}")
                return
            person_id = self.enrolling_card_for
            self.db.add_card(uid, person_id)
            self.db.audit(
                person_id, uid, None, "ADMIN", "Cartão vinculado"
            )
            self.enrolling_card_for = None
            self.log(f"Cartão {uid} vinculado.")
            self.refresh_all()

        elif event == "CARD":
            uid = normalize_uid(msg.get("uid", ""))
            if not uid:
                return
            self.pending_cards[uid] = time.monotonic()
            self.log(f"Cartão apresentado: {uid}. Apresente a digital.")
            self.evaluate_access()

        elif event == "FP_MATCH":
            try:
                slot = int(msg.get("slot"))
            except (TypeError, ValueError):
                return
            self.pending_fingerprints[slot] = time.monotonic()
            self.log(f"Digital reconhecida: slot {slot}.")
            self.evaluate_access()

        elif event == "FP_ENROLLED":
            if self.enrolling_fingerprint_for is None:
                self.log("Digital cadastrada no sensor sem vínculo pendente.")
                return
            person_id, expected_slot = self.enrolling_fingerprint_for
            slot = int(msg.get("slot", -1))
            if slot != expected_slot:
                self.log("Slot biométrico inesperado; vínculo não criado.")
                return
            self.db.add_fingerprint(slot, person_id)
            self.db.audit(
                person_id, None, slot, "ADMIN", "Digital cadastrada"
            )
            self.enrolling_fingerprint_for = None
            self.log(f"Digital vinculada à pessoa no slot {slot}.")
            self.refresh_all()

        elif event == "FP_NO_MATCH":
            self.log("Digital não reconhecida.")

        elif event == "ENROLL_STATUS":
            self.log(str(msg.get("detail", "Status biométrico")))

        elif event in ("ERROR", "CONNECTION_ERROR"):
            self.log("Erro: " + str(msg.get("detail", "desconhecido")))
            self.status.configure(text="Verifique o sensor/conexão")

    def evaluate_access(self):
        now = time.monotonic()

        self.pending_cards = {
            uid: stamp for uid, stamp in self.pending_cards.items()
            if now - stamp <= PAIR_WINDOW
        }
        self.pending_fingerprints = {
            slot: stamp for slot, stamp in self.pending_fingerprints.items()
            if now - stamp <= PAIR_WINDOW
        }

        for uid, card_time in list(self.pending_cards.items()):
            card = self.db.card(uid)

            if not card or not card["card_active"] or not card["person_active"]:
                person_id = card["person_id"] if card else None
                self.db.audit(
                    person_id, uid, None, "NEGADO",
                    "Cartão desconhecido/bloqueado ou pessoa inativa"
                )
                self.log(f"ACESSO NEGADO: cartão {uid} não autorizado.")
                self.send({"cmd": "ACCESS_DECISION", "allow": False})
                self.pending_cards.pop(uid, None)
                continue

            for slot, fp_time in list(self.pending_fingerprints.items()):
                fp = self.db.fingerprint(slot)
                if not fp or not fp["fp_active"] or not fp["person_active"]:
                    continue

                if abs(card_time - fp_time) > PAIR_WINDOW:
                    continue

                self.pending_cards.pop(uid, None)
                self.pending_fingerprints.pop(slot, None)

                if card["person_id"] == fp["person_id"]:
                    self.db.audit(
                        card["person_id"], uid, slot, "AUTORIZADO",
                        "Cartão e digital válidos para a mesma pessoa"
                    )
                    self.log(
                        f"ACESSO AUTORIZADO: {card['name']} — cartão + digital."
                    )
                    self.send({
                        "cmd": "ACCESS_DECISION",
                        "allow": True
                    })
                else:
                    self.db.audit(
                        card["person_id"], uid, slot, "NEGADO",
                        "Cartão e digital pertencem a pessoas diferentes"
                    )
                    self.log("ACESSO NEGADO: credenciais não correspondem.")
                    self.send({
                        "cmd": "ACCESS_DECISION",
                        "allow": False
                    })
                return

    def process_events(self):
        try:
            while True:
                self.handle_event(self.events.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self.process_events)

    def refresh_all(self):
        for tree_name in ("people_tree", "cards_tree", "fp_tree"):
            tree = getattr(self, tree_name, None)
            if tree:
                tree.delete(*tree.get_children())

        if hasattr(self, "people_tree"):
            for p in self.db.people():
                self.people_tree.insert("", "end", values=(
                    p["id"], p["name"],
                    "ATIVO" if p["active"] else "INATIVO"
                ))

        if hasattr(self, "cards_tree"):
            for c in self.db.cards():
                self.cards_tree.insert("", "end", values=(
                    c["uid"], c["name"],
                    "ATIVO" if c["active"] else "BLOQUEADO"
                ))

        if hasattr(self, "fp_tree"):
            for f in self.db.fingerprints():
                self.fp_tree.insert("", "end", values=(
                    f["slot"], f["name"],
                    "ATIVO" if f["active"] else "BLOQUEADO"
                ))

        self.refresh_audit()

    def refresh_audit(self):
        if not hasattr(self, "audit_tree"):
            return
        self.audit_tree.delete(*self.audit_tree.get_children())

        for event in self.db.events():
            stamp = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(event["timestamp"])
            )
            self.audit_tree.insert("", "end", values=(
                stamp, event["name"], event["uid"] or "-",
                event["fp_slot"] or "-", event["decision"], event["detail"]
            ))

    def close(self):
        self.serial.disconnect()
        self.destroy()


if __name__ == "__main__":
    GuyAccess().mainloop()
