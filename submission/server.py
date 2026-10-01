"""Start one application server for a bounded check and clean up its descendants."""

import os
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from processes import ProcessGroup


@contextmanager
def application_server(workspace: Path, environment: dict[str, str]):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    environment = environment | {"PORT": str(port), "BASE_URL": f"http://127.0.0.1:{port}"}
    arguments = ["cmd.exe", "/d", "/c", "npm start"] if os.name == "nt" else ["npm", "start"]
    with tempfile.TemporaryFile() as output, ProcessGroup() as group:
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
            deadline = time.monotonic() + 30
            ready = False
            while process.poll() is None and time.monotonic() < deadline:
                with socket.socket() as connection:
                    connection.settimeout(0.2)
                    ready = connection.connect_ex(("127.0.0.1", port)) == 0
                if ready:
                    break
                time.sleep(0.2)
            yield environment, ready, output
        finally:
            group.close()
            process.wait()
            deadline = time.monotonic() + 3
            while ready and time.monotonic() < deadline:
                with socket.socket() as connection:
                    connection.settimeout(0.2)
                    if connection.connect_ex(("127.0.0.1", port)) != 0:
                        break
                time.sleep(0.02)
