"""User data is never written to a read-only installation directory."""
from pathlib import Path
import os
import sys
from platformdirs import user_data_path


def data_directory():
    return Path(os.environ.get('WILDFIRE_PLANNING_DATA', user_data_path('WildfirePlanner', appauthor=False)))


def resource_directory():
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / 'planning_resources'
    return Path(__file__).resolve().parents[3] / 'data' / 'planning-packs-v1'


class InstanceLock:
    """OS releases the lock on crashes; stale lock files need no deletion."""
    def __init__(self, root):
        root.mkdir(parents=True, exist_ok=True)
        self.file = (root / 'instance.lock').open('a+b')
        try:
            # Windows byte-range locks also reject a read of the locked byte.
            # Initialize only a zero-length file, without touching a live lock.
            if os.fstat(self.file.fileno()).st_size == 0:
                self.file.write(b'0')
                self.file.flush()
            self.file.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError('This user-data directory is already open in another planner process')

    def close(self):
        self.file.close()
