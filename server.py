"""Start one application server for a bounded check and clean up its descendants."""

import os
import socket
import subprocess
import tempfile
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path

from processes import ProcessGroup


@contextmanager
def application_server(workspace: Path, environment: dict[str, str], deadline: float | None = None):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = environment | {"PORT": str(port), "BASE_URL": f"http://127.0.0.1:{port}"}
    arguments = ["cmd.exe", "/d", "/c", "npm start"] if os.name == "nt" else ["npm", "start"]
    with ExitStack() as resources, tempfile.TemporaryFile() as output, ProcessGroup() as group:
        if deadline is not None and time.monotonic() >= deadline:
            yield environment, False, output
            return
        database_path = environment.get("ARC_DB_FILE") or environment.get("DATABASE_FILE")
        if not database_path:
            directory = resources.enter_context(tempfile.TemporaryDirectory(prefix="agent-database-"))
            database_path = str(Path(directory) / "application.db")
        environment = environment | {
            "ARC_DB_FILE": str((workspace / "backend" / (database_path.strip() or "database.db")).resolve())
        }
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
