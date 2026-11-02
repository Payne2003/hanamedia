import os


class InstanceLock:
    """OS-level lock; released by the OS even after a crash."""
    def __init__(self, path):
        self.file = open(path, "a+b")
        try:
            self.file.seek(0)
            if not self.file.read(1):
                self.file.write(b"0")
                self.file.flush()
            self.file.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self.file.close()
            raise

    def close(self):
        if not self.file.closed:
            self.file.close()
