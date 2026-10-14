
import argparse
import sys
import time
import sqlite3
import traceback
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageTk

from guy_common import (
    COLOR4, FACE_DB, MODEL_DIR, access_people, apply_ttk_theme, brand_bar,
    download_face_models, window_icon
)

DB_PATH = FACE_DB

SAMPLES_REQUIRED = 10
MATCH_THRESHOLD = 0.363
CAMERA_INDEX = 0


def download_models():
    # Fonte única em guy_common (usada também pelo main.py na instalação).
    download_face_models()


def connect_database():
    connection = sqlite3.connect(DB_PATH)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")

    connection.executescript("""
        CREATE TABLE IF NOT EXISTS persons (
            person_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            face_feature BLOB NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS face_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id TEXT,
            name TEXT,
            event_type TEXT NOT NULL,
            confidence REAL,
            timestamp TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_face_events_timestamp
        ON face_events(timestamp);
    """)

    connection.commit()
    return connection


def normalize_feature(feature):
    feature = np.asarray(feature, dtype=np.float32).reshape(-1)
    norm = np.linalg.norm(feature)

    if norm == 0:
        raise ValueError("Não foi possível extrair características faciais.")

    return feature / norm


class GuyFaceApp:
    def __init__(self, root, initial_person_id=None):
        self.root = root
        self.root.title("GUY ACCESS | Reconhecimento Facial")
        self.root.geometry("1050x830")
        self.root.minsize(850, 700)
        window_icon(self.root)

        self.connection = connect_database()
        self.camera = None
        self.running = False
        self.recognition_enabled = True
        self.enrollment = None

        self.last_logged_identity = None
        self.last_event_time = 0.0

        self.detector = None
        self.recognizer = None
        self.known_faces = {}

        self.status = tk.StringVar(value="Inicializando...")
        self.person_count = tk.StringVar(value="Cadastros: 0")
        self.result_text = tk.StringVar(
            value="Aguardando reconhecimento facial"
        )

        self.build_interface()

        try:
            download_models()
            self.load_models()
            self.load_faces()
            self.start_camera()
        except Exception:
            self.connection.close()
            raise

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(20, self.update_frame)

        if initial_person_id:
            self.root.after(
                800, lambda: self.begin_enrollment(initial_person_id)
            )

    def build_interface(self):
        style = apply_ttk_theme(self.root)
        style.configure("TButton", font=("Segoe UI", 10), padding=8)

        header = brand_bar(
            self.root, "G.U.Y. ACCESS",
            "Módulo de reconhecimento facial local"
        )
        header.pack(fill="x")

        main = tk.Frame(self.root, bg=COLOR4, padx=16, pady=16)
        main.pack(fill="both", expand=True)

        self.video = tk.Label(
            main,
            bg="#000000",
            fg="#ffffff",
            text="Inicializando webcam...",
            font=("Segoe UI", 12)
        )
        self.video.pack(fill="both", expand=True)

        result = tk.Frame(main, bg="#351834", padx=12, pady=12)
        result.pack(fill="x", pady=(12, 8))

        tk.Label(
            result,
            textvariable=self.result_text,
            font=("Segoe UI", 15, "bold"),
            fg="#ffffff",
            bg="#351834"
        ).pack(anchor="w")

        tk.Label(
            result,
            textvariable=self.status,
            font=("Segoe UI", 9),
            fg="#d9c2d8",
            bg="#351834",
            wraplength=900,
            justify="left"
        ).pack(anchor="w", pady=(5, 0))

        controls = tk.Frame(main, bg="#1b0c1a")
        controls.pack(fill="x", pady=(8, 0))

        ttk.Button(
            controls,
            text="Cadastrar rosto",
            command=self.begin_enrollment
        ).pack(side="left", padx=(0, 8))

        ttk.Button(
            controls,
            text="Atualizar cadastros",
            command=self.load_faces
        ).pack(side="left", padx=8)

        self.recognition_button = ttk.Button(
            controls,
            text="Pausar reconhecimento",
            command=self.toggle_recognition
        )
        self.recognition_button.pack(side="left", padx=8)

        tk.Label(
            controls,
            textvariable=self.person_count,
            bg="#1b0c1a",
            fg="#ffffff",
            font=("Segoe UI", 10)
        ).pack(side="right", padx=8)

        tk.Label(
            main,
            text=(
                "O reconhecimento facial não libera acesso. "
                "RFID e impressão digital continuam obrigatórios."
            ),
            bg="#1b0c1a",
            fg="#cbb8cb",
            wraplength=900,
            justify="left",
            font=("Segoe UI", 9)
        ).pack(anchor="w", pady=(12, 0))

    def load_models(self):
        self.status.set("Carregando modelos faciais...")
        self.root.update_idletasks()

        try:
            self.detector = cv2.FaceDetectorYN.create(
                str(MODEL_DIR / "yunet.onnx"),
                "",
                (320, 320),
                0.85,
                0.3,
                5000
            )

            self.recognizer = cv2.FaceRecognizerSF.create(
                str(MODEL_DIR / "sface.onnx"),
                ""
            )
        except cv2.error as exc:
            raise RuntimeError(
                f"Não foi possível carregar os modelos em {MODEL_DIR}.\n"
                f"OpenCV {cv2.__version__}: {exc}\n\n"
                "Causas prováveis: arquivo de modelo corrompido (apague a "
                "pasta de modelos e rode main.py de novo) ou versão do "
                "OpenCV incompatível (rode: python main.py --reinstall). "
                "Diagnóstico: python main.py check"
            ) from exc

    def load_faces(self):
        rows = self.connection.execute(
            "SELECT person_id, name, face_feature FROM persons"
        ).fetchall()

        loaded = {}

        for person_id, name, blob in rows:
            feature = np.frombuffer(blob, dtype=np.float32).copy()

            if feature.size:
                loaded[person_id] = {
                    "name": name,
                    "feature": normalize_feature(feature)
                }

        self.known_faces = loaded
        self.person_count.set(f"Cadastros: {len(loaded)}")

        self.status.set(
            f"{len(loaded)} cadastro(s) facial(is) carregado(s)."
        )

    def open_camera(self):
        backends = [
            ("DirectShow", cv2.CAP_DSHOW),
            ("Media Foundation", cv2.CAP_MSMF),
            ("Automático", cv2.CAP_ANY)
        ]

        errors = []

        for backend_name, backend in backends:
            camera = cv2.VideoCapture(CAMERA_INDEX, backend)

            if not camera.isOpened():
                camera.release()
                errors.append(f"{backend_name}: não abriu")
                continue

            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            if backend != cv2.CAP_MSMF:
                camera.set(
                    cv2.CAP_PROP_FOURCC,
                    cv2.VideoWriter_fourcc(*"MJPG")
                )

            success = False

            for _ in range(8):
                ok, frame = camera.read()

                if (
                    ok
                    and frame is not None
                    and frame.ndim == 3
                    and frame.shape[2] == 3
                    and frame.size > 0
                ):
                    success = True
                    break

                time.sleep(0.1)

            if success:
                self.status.set(
                    f"Webcam conectada usando {backend_name}."
                )
                return camera

            camera.release()
            errors.append(f"{backend_name}: sem imagem válida")

        raise RuntimeError(
            "Não foi possível obter imagem da webcam.\n"
            + "\n".join(errors)
            + "\nVerifique as permissões de câmera do Windows "
            "e feche outros aplicativos que estejam usando a webcam."
        )

    def start_camera(self):
        self.camera = self.open_camera()
        self.running = True

    def choose_person(self, preselected_id=None):
        """Define quem será cadastrado. Retorna (person_id, name) ou None.

        O GUY ACCESS é a fonte das identidades: havendo pessoas cadastradas
        lá, o rosto só pode ser associado a uma delas (evita ID digitado
        errado). Sem GUY ACCESS utilizável, mantém o comportamento original
        de ID e nome digitados.
        """
        people = access_people()

        if people is None:
            messagebox.showwarning(
                "Cadastro facial",
                "Não foi possível ler o banco do GUY ACCESS.\n"
                "O cadastro continuará com ID e nome digitados manualmente."
            )
        elif people:
            if preselected_id is not None:
                match = next(
                    (p for p in people if p["id"] == str(preselected_id)),
                    None
                )

                if match:
                    return match["id"], match["name"]

                messagebox.showwarning(
                    "Cadastro facial",
                    f"A pessoa ID {preselected_id} não existe no GUY ACCESS. "
                    "Escolha outra na lista."
                )

            return self.pick_person_dialog(people)

        person_id = simpledialog.askstring(
            "Cadastro facial",
            "Informe o ID da pessoa no GUY ACCESS:",
            parent=self.root
        )

        if not person_id or not person_id.strip():
            return None

        name = simpledialog.askstring(
            "Cadastro facial",
            "Informe o nome da pessoa:",
            parent=self.root
        )

        if not name or not name.strip():
            return None

        return person_id.strip(), name.strip()

    def pick_person_dialog(self, people):
        result = {}

        dialog = tk.Toplevel(self.root)
        dialog.title("Cadastro facial")
        dialog.configure(bg=COLOR4)
        dialog.resizable(False, False)
        dialog.transient(self.root)

        ttk.Label(
            dialog, text="Pessoa cadastrada no GUY ACCESS:"
        ).pack(anchor="w", padx=16, pady=(16, 6))

        labels = [
            f"{p['id']} — {p['name']}" + ("" if p["active"] else " (inativa)")
            for p in people
        ]

        box = ttk.Combobox(
            dialog, values=labels, state="readonly", width=44
        )
        box.pack(padx=16, pady=4)

        def confirm():
            index = box.current()

            if index < 0:
                messagebox.showwarning(
                    "Cadastro facial", "Selecione uma pessoa.", parent=dialog
                )
                return

            result["person"] = people[index]
            dialog.destroy()

        buttons = ttk.Frame(dialog)
        buttons.pack(pady=16)

        ttk.Button(
            buttons, text="Continuar", command=confirm
        ).pack(side="left", padx=6)

        ttk.Button(
            buttons, text="Cancelar", command=dialog.destroy
        ).pack(side="left", padx=6)

        dialog.wait_visibility()
        dialog.grab_set()
        self.root.wait_window(dialog)

        person = result.get("person")
        return (person["id"], person["name"]) if person else None

    def begin_enrollment(self, preselected_id=None):
        if self.enrollment is not None:
            messagebox.showinfo(
                "Cadastro",
                "Já existe um cadastro em andamento."
            )
            return

        chosen = self.choose_person(preselected_id)

        if chosen is None:
            return

        person_id, name = chosen

        exists = self.connection.execute(
            "SELECT name FROM persons WHERE person_id = ?",
            (person_id,)
        ).fetchone()

        if exists and not messagebox.askyesno(
            "Atualizar cadastro",
            f"O ID {person_id} pertence a {exists[0]}.\n"
            "Deseja substituir o cadastro facial?"
        ):
            return

        self.enrollment = {
            "person_id": person_id,
            "name": name,
            "features": []
        }

        self.result_text.set("Preparando cadastro...")
        self.status.set(
            "Olhe para a câmera. Mantenha o rosto visível e iluminado."
        )

    def extract_feature(self, frame, face):
        aligned = self.recognizer.alignCrop(frame, face)
        feature = self.recognizer.feature(aligned)
        return normalize_feature(feature)

    def identify(self, feature):
        best_person = None
        best_score = -1.0

        for person_id, person in self.known_faces.items():
            score = float(np.dot(feature, person["feature"]))

            if score > best_score:
                best_score = score
                best_person = person_id

        if (
            best_person is not None
            and best_score >= MATCH_THRESHOLD
        ):
            person = self.known_faces[best_person]
            return best_person, person["name"], best_score

        return None, "Desconhecido", best_score

    def log_event(self, person_id, name, event_type, confidence):
        timestamp = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )

        self.connection.execute(
            """
            INSERT INTO face_events
            (person_id, name, event_type, confidence, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (person_id, name, event_type, confidence, timestamp)
        )

        self.connection.commit()

    def save_enrollment(self):
        enrollment = self.enrollment
        features = np.stack(enrollment["features"])
        mean_feature = normalize_feature(np.mean(features, axis=0))

        timestamp = datetime.now().astimezone().isoformat(
            timespec="seconds"
        )

        self.connection.execute(
            """
            INSERT OR REPLACE INTO persons
            (person_id, name, face_feature, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                enrollment["person_id"],
                enrollment["name"],
                mean_feature.tobytes(),
                timestamp
            )
        )

        self.connection.commit()

        self.log_event(
            enrollment["person_id"],
            enrollment["name"],
            "FACE_ENROLLED",
            None
        )

        name = enrollment["name"]
        self.enrollment = None
        self.load_faces()

        self.result_text.set(f"Cadastro concluído: {name}")
        self.status.set("Representação facial salva localmente.")

    def process_face(self, frame, face):
        feature = self.extract_feature(frame, face)

        if self.enrollment is not None:
            enrollment = self.enrollment
            enrollment["features"].append(feature)
            collected = len(enrollment["features"])

            self.result_text.set(
                f"Cadastrando {enrollment['name']}: "
                f"{collected}/{SAMPLES_REQUIRED} amostras"
            )

            if collected >= SAMPLES_REQUIRED:
                self.save_enrollment()

            return "CADASTRANDO", (0, 200, 200)

        if not self.recognition_enabled:
            return "RECONHECIMENTO PAUSADO", (180, 180, 0)

        person_id, name, score = self.identify(feature)

        if person_id is None:
            label = "Desconhecido"
            identity = "UNKNOWN"
            color = (0, 0, 255)
        else:
            label = f"{name} | ID: {person_id}"
            identity = person_id
            color = (0, 200, 0)

        now = time.monotonic()

        if (
            identity != self.last_logged_identity
            and now - self.last_event_time >= 2
        ):
            self.log_event(
                person_id,
                name,
                "FACE_MATCH" if person_id is not None else "FACE_UNKNOWN",
                score if score >= 0 else None
            )

            self.last_logged_identity = identity
            self.last_event_time = now

        self.result_text.set(
            f"{label} | Similaridade: {score:.3f}"
            if score >= 0
            else label
        )

        self.status.set(
            "Identificação facial apenas. Nenhum acesso autorizado."
        )

        return label, color

    def update_frame(self):
        if not self.running or self.camera is None:
            return

        success, frame = self.camera.read()

        if not success or frame is None:
            self.status.set("Falha na leitura da webcam.")
            self.root.after(100, self.update_frame)
            return

        frame = cv2.flip(frame, 1)
        height, width = frame.shape[:2]

        try:
            self.detector.setInputSize((width, height))
            _, faces = self.detector.detect(frame)

            if (
                faces is not None
                and self.enrollment is not None
                and len(faces) > 1
            ):
                # Com mais de um rosto, as amostras de pessoas diferentes
                # seriam misturadas na mesma representação facial.
                self.result_text.set(
                    "Cadastro em espera: deixe apenas uma pessoa na imagem."
                )
                self.status.set(
                    "Mais de um rosto detectado. Nenhuma amostra coletada."
                )

                for extra in faces:
                    ex, ey, ew, eh = extra[:4].astype(int)
                    cv2.rectangle(
                        frame, (ex, ey), (ex + ew, ey + eh), (0, 0, 255), 2
                    )

                faces = None

            if faces is not None:
                for face in faces:
                    x, y, w, h = face[:4].astype(int)

                    try:
                        label, color = self.process_face(frame, face)

                        cv2.rectangle(
                            frame,
                            (x, y),
                            (x + w, y + h),
                            color,
                            2
                        )

                        cv2.putText(
                            frame,
                            label[:55],
                            (x, max(25, y - 10)),
                            cv2.FONT_HERSHEY_SIMPLEX,
                            0.55,
                            color,
                            2,
                            cv2.LINE_AA
                        )

                    except (cv2.error, ValueError, FloatingPointError) as error:
                        self.status.set(
                            f"Falha no processamento facial: {error}"
                        )

        except cv2.error as error:
            self.status.set(f"Erro do OpenCV: {error}")

        # OpenCV fornece a imagem em BGR.
        # O Pillow precisa receber RGB.
        # Não use cv2.imencode() depois de converter para RGB,
        # pois isso pode trocar novamente os canais de cor.
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        pil_image = Image.fromarray(rgb)
        photo = ImageTk.PhotoImage(image=pil_image)

        self.video.configure(image=photo, text="")
        self.video.image = photo

        self.root.after(15, self.update_frame)

    def toggle_recognition(self):
        self.recognition_enabled = not self.recognition_enabled

        if self.recognition_enabled:
            self.recognition_button.configure(
                text="Pausar reconhecimento"
            )
            self.status.set("Reconhecimento ativado.")
        else:
            self.recognition_button.configure(
                text="Retomar reconhecimento"
            )
            self.status.set("Reconhecimento pausado.")

    def close(self):
        self.running = False

        if self.camera is not None:
            self.camera.release()

        self.connection.close()
        self.root.destroy()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--person-id",
        help="ID da pessoa no GUY ACCESS para iniciar o cadastro facial"
    )
    args = parser.parse_args()

    root = tk.Tk()

    try:
        GuyFaceApp(root, initial_person_id=args.person_id)
    except Exception as error:
        traceback.print_exc()
        messagebox.showerror(
            "Erro ao iniciar GUY ACCESS",
            f"{error}\n\n"
            "Confira as dependências, os modelos e a webcam."
        )
        root.destroy()
        sys.exit(1)

    root.mainloop()


if __name__ == "__main__":
    main()
