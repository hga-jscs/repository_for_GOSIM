"""Own a command's descendants so background servers do not survive a tool call."""

import ctypes
import os


class ProcessGroup:
    def __init__(self):
        self.handle = None
        self.process = None
        if os.name == "nt":
            from ctypes import wintypes

            class BasicLimits(ctypes.Structure):
                _fields_ = [
                    ("process_time", ctypes.c_longlong),
                    ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD),
                    ("minimum", ctypes.c_size_t),
                    ("maximum", ctypes.c_size_t),
                    ("process_count", wintypes.DWORD),
                    ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD),
                    ("scheduling", wintypes.DWORD),
                ]

            class ExtendedLimits(ctypes.Structure):
                _fields_ = [("basic", BasicLimits), ("io", ctypes.c_ulonglong * 6), ("memory", ctypes.c_size_t * 4)]

            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.CreateJobObjectW.restype = wintypes.HANDLE
            self.kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
            self.kernel.SetInformationJobObject.argtypes = [
                wintypes.HANDLE,
                ctypes.c_int,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            self.kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.CreateJobObjectW(None, None)
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000
            if not self.handle or not self.kernel.SetInformationJobObject(
                self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
            ):
                self.close()
                raise OSError("Unable to create a process cleanup job")

    def attach(self, process) -> None:
        self.process = process
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            process.kill()
            process.wait()
            self.close()
            raise OSError("Unable to isolate command descendants in the cleanup job")

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
        elif os.name != "nt" and self.process is not None:
            ctypes.CDLL(None).kill(-self.process.pid, 9)
        self.process = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
