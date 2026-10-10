"""Start one application server for a bounded check and clean up its descendants."""

import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
from contextlib import ExitStack, closing, contextmanager
from pathlib import Path

from processes import ProcessGroup


@contextmanager
def application_server(workspace: Path, environment: dict[str, str], deadline: float | None = None,
                       *, copy_existing_database: bool = False, startup_state: dict | None = None):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = environment | {"PORT": str(port), "BASE_URL": f"http://127.0.0.1:{port}"}
    arguments = ["cmd.exe", "/d", "/c", "npm start"] if os.name == "nt" else ["npm", "start"]
    with ExitStack() as resources, tempfile.TemporaryFile() as output, ProcessGroup() as group:
        if deadline is not None and time.monotonic() >= deadline:
            if startup_state is not None:
                startup_state["timed_out"] = True
            yield environment, False, output
            return
        database_path = environment.get("ARC_DB_FILE") or environment.get("DATABASE_FILE")
        original_database = (workspace / "backend" / ((database_path or "database.db").strip() or "database.db")).resolve()
        if copy_existing_database or not database_path:
            directory = resources.enter_context(tempfile.TemporaryDirectory(prefix="agent-database-"))
            database_path = str(Path(directory) / "application.db")
            if copy_existing_database and original_database.is_file():
                snapshot_deadline = min(time.monotonic() + 10, deadline if deadline is not None else float("inf"))

                def snapshot_progress(status, remaining, total):
                    if time.monotonic() >= snapshot_deadline:
                        raise sqlite3.OperationalError("Database snapshot deadline reached")

                try:
                    with closing(sqlite3.connect(original_database.as_uri() + "?mode=ro", uri=True)) as original:
                        with closing(sqlite3.connect(database_path)) as snapshot:
                            original.backup(snapshot, pages=256, progress=snapshot_progress, sleep=0.05)
                except sqlite3.Error:
                    shutil.copy2(original_database, database_path)
                    journal = Path(str(original_database) + "-wal")
                    if journal.is_file():
                        shutil.copy2(journal, database_path + "-wal")
        environment = environment | {
            "ARC_DB_FILE": str((workspace / "backend" / (database_path.strip() or "database.db")).resolve())
        }
        if copy_existing_database:
            environment["DATABASE_FILE"] = environment["ARC_DB_FILE"]
        process = subprocess.Popen(
            arguments,
            cwd=workspace / "backend",
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        group.attach(process)
        try:
            startup_deadline = min(time.monotonic() + 30, deadline if deadline is not None else float("inf"))
            ready = False
            while process.poll() is None and time.monotonic() < startup_deadline:
                with socket.socket() as connection:
                    connection.settimeout(max(0.001, min(0.2, startup_deadline - time.monotonic())))
                    ready = connection.connect_ex(("127.0.0.1", port)) == 0
                if ready:
                    break
                time.sleep(max(0, min(0.2, startup_deadline - time.monotonic())))
            if startup_state is not None:
                startup_state["timed_out"] = not ready and process.poll() is None
            yield environment, ready, output
        finally:
            group.close()
            process.wait()
            cleanup_deadline = time.monotonic() + 3
            while ready and time.monotonic() < cleanup_deadline:
                with socket.socket() as connection:
                    connection.settimeout(0.2)
                    if connection.connect_ex(("127.0.0.1", port)) != 0:
                        break
                time.sleep(0.02)
