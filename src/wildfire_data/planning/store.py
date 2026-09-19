"""SQLite is authoritative. FULL-sync transactions acknowledge only committed data."""
from contextlib import contextmanager
import json
from pathlib import Path
import shutil
import sqlite3
import threading
from uuid import uuid4

from wildfire_data.planning.contracts import canonical, digest, now

CAP_BYTES = 20_000_000_000
SCHEMA_VERSION = 1


class Conflict(ValueError):
    pass


class StorageFull(OSError):
    pass


def tree_bytes(root):
    return sum(p.stat().st_size for p in Path(root).rglob('*') if p.is_file() and not p.is_symlink()) if Path(root).exists() else 0


class Store:
    def __init__(self, root, *, installed_bytes=0, cap_bytes=CAP_BYTES):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'scenarios.sqlite3'
        self.installed_bytes, self.cap_bytes = installed_bytes, cap_bytes
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        try:
            self._migrate()
        except BaseException:
            self.db.close()
            raise
        # A single app process holds an OS lock before opening this store.
        with self.transaction():
            self.db.execute("UPDATE scenarios SET status='paused', generation=generation+1 WHERE status='running'")

    def _migrate(self):
        version = self.db.execute('PRAGMA user_version').fetchone()[0]
        if version == SCHEMA_VERSION:
            return
        has_tables = self.db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone()
        if has_tables:
            backup = self.root / f'pre-migration-v{version}-{uuid4().hex}.sqlite3'
            self.admit(self.path.stat().st_size * 2)
            with sqlite3.connect(backup) as destination:
                self.db.backup(destination)
            # No historical schema migrations exist yet. Never guess at an
            # unknown/future database, and retain the verified SQLite backup.
            raise ValueError(f'Unsupported database version {version}; backup retained at {backup.name}')
        if version != 0:
            raise ValueError('Unsupported empty database version')
        with self.transaction():
            self.db.execute('''CREATE TABLE scenarios (
                id TEXT PRIMARY KEY, revision INTEGER NOT NULL, parent_id TEXT,
                name TEXT NOT NULL, definition TEXT NOT NULL, pack_snapshot TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                status TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 0,
                checkpoint INTEGER NOT NULL DEFAULT -1, playback_hour INTEGER NOT NULL DEFAULT 0,
                error TEXT, imported INTEGER NOT NULL DEFAULT 0)''')
            self.db.execute('''CREATE TABLE frames (scenario_id TEXT NOT NULL REFERENCES scenarios(id),
                hour INTEGER NOT NULL, payload TEXT NOT NULL, sha256 TEXT NOT NULL,
                PRIMARY KEY(scenario_id,hour))''')
            self.db.execute('''CREATE TABLE requests (request_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                scenario_id TEXT NOT NULL REFERENCES scenarios(id))''')
            self.db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')

    @contextmanager
    def transaction(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self.db.execute('COMMIT')
            except BaseException:
                if self.db.in_transaction:
                    self.db.execute('ROLLBACK')
                raise

    def storage(self):
        used = self.installed_bytes + tree_bytes(self.root)
        return {'used_bytes': used, 'cap_bytes': self.cap_bytes, 'remaining_bytes': max(0, self.cap_bytes-used),
                'warning': 'Storage is above 90% of the 20 GB cap; nothing will be removed automatically.' if used > .9*self.cap_bytes else None}

    def admit(self, payload_bytes):
        # Reserve for DB pages, full WAL copies and commit overhead, not just JSON.
        required = payload_bytes * 3 + 131072
        if self.storage()['used_bytes'] + required > self.cap_bytes:
            raise StorageFull('Managed-storage cap would be exceeded. No saved cases or packs were deleted.')
        if shutil.disk_usage(self.root).free < required + 50_000_000:
            raise StorageFull('Insufficient disk space for an acknowledged save. Last committed checkpoint retained.')

    def get(self, identity):
        with self.lock:
            row = self.db.execute('SELECT * FROM scenarios WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise KeyError('Scenario not found')
            result = dict(row)
            for key in ('definition', 'pack_snapshot'):
                result[key] = json.loads(result[key])
            return result

    def list(self):
        with self.lock:
            return [dict(row) for row in self.db.execute('SELECT id,name,revision,parent_id,status,checkpoint,playback_hour,created_at,updated_at,error FROM scenarios ORDER BY updated_at DESC')]

    def frames(self, identity):
        self.get(identity)
        with self.lock:
            rows = self.db.execute('SELECT hour,payload,sha256 FROM frames WHERE scenario_id=? ORDER BY hour', (identity,)).fetchall()
            results = []
            for row in rows:
                value = json.loads(row['payload'])
                if digest(value) != row['sha256']:
                    raise ValueError('Stored frame checksum mismatch; results cannot be trusted')
                results.append({'hour': row['hour'], 'result': value})
            return results

    def mutate(self, request_id, operation, arguments, callback):
        fingerprint = digest({'operation': operation, 'arguments': arguments})
        with self.transaction():
            previous = self.db.execute('SELECT * FROM requests WHERE request_id=?', (str(request_id),)).fetchone()
            if previous:
                if previous['fingerprint'] != fingerprint:
                    raise Conflict('Request ID was already used with different inputs')
                return self.get(previous['scenario_id']), False
            self.admit(len(canonical(arguments).encode()))
            identity = callback()
            self.db.execute('INSERT INTO requests VALUES (?,?,?)', (str(request_id), fingerprint, identity))
            return self.get(identity), True

    def insert(self, name, definition, snapshot, *, parent=None, frames=(), cursor=0, imported=False, created_at=None):
        identity, timestamp = str(uuid4()), now()
        self.admit(len(canonical(snapshot).encode()) + len(canonical(frames).encode()))
        self.db.execute('''INSERT INTO scenarios
            (id,revision,parent_id,name,definition,pack_snapshot,created_at,updated_at,status,checkpoint,playback_hour,imported)
            VALUES (?,1,?,?,?,?,?,?,'paused',?,?,?)''',
            (identity, parent, name, canonical(definition), canonical(snapshot), created_at or timestamp, timestamp, len(frames)-1, cursor, int(imported)))
        for frame in frames:
            self.db.execute('INSERT INTO frames VALUES (?,?,?,?)', (identity, frame['hour'], canonical(frame['result']), digest(frame['result'])))
        return identity

    def require_revision(self, identity, revision):
        scenario = self.get(identity)
        if scenario['revision'] != revision:
            raise Conflict('Stale scenario revision. Reload before making changes; acknowledged saves were preserved.')
        return scenario

    def change(self, identity, **values):
        allowed = {'name', 'definition', 'status', 'generation', 'playback_hour', 'error'}
        if not values.keys() <= allowed:
            raise ValueError('Invalid scenario update')
        values.update(updated_at=now())
        self.db.execute('UPDATE scenarios SET revision=revision+1,' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?',
                        (*values.values(), identity))

    def commit_frame(self, identity, generation, hour, result):
        encoded = canonical(result)
        with self.transaction():
            row = self.get(identity)
            if row['generation'] != generation or row['status'] != 'running':
                return False
            if hour != row['checkpoint'] + 1:
                raise Conflict('Nonsequential or duplicate worker checkpoint')
            self.admit(len(encoded.encode()))
            self.db.execute('INSERT INTO frames VALUES (?,?,?,?)', (identity, hour, encoded, digest(result)))
            # Frame commits do not revise user-controlled inputs/cursor. This
            # lets a stale frame poll coexist safely with an input revision CAS.
            self.db.execute('UPDATE scenarios SET checkpoint=?,updated_at=? WHERE id=?', (hour, now(), identity))
        return True

    def finish(self, identity, generation, error=None):
        with self.transaction():
            row = self.get(identity)
            if row['generation'] == generation and row['status'] == 'running':
                self.db.execute('UPDATE scenarios SET status=?,error=?,updated_at=? WHERE id=?',
                    ('failed' if error else 'complete', error, now(), identity))

    def close(self):
        with self.lock:
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            self.db.close()
