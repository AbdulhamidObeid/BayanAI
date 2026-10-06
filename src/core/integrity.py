"""Canonical integrity payloads and private, authenticated SQLite storage.
Hashes prove byte integrity; they do not prove theological correctness.
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path
from src.core.source_policy import ROOT, load_policy

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))

def fingerprint(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()

class AuthenticatedStore:
    def __init__(self, directory=None, measurements=None):
        self.measurements = measurements
        self.directory = Path(directory or os.getenv('BAYAN_PRIVATE_STORE') or ROOT / load_policy()['private_store'])
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        key_path = self.directory / 'integrity.key'
        if not key_path.exists():
            temporary = self.directory / ('key-'+secrets.token_hex(12)+'.tmp')
            fd = os.open(str(temporary), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as f:
                f.write(secrets.token_bytes(32))
            try:
                # Publishing an already complete file avoids another process reading an empty key.
                os.link(str(temporary), str(key_path))
            except FileExistsError:
                pass
            finally:
                temporary.unlink()
        self.key = key_path.read_bytes()
        if len(self.key) != 32:
            raise ValueError('Invalid integrity key')
        self.database = self.directory / 'records.db'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (kind TEXT, id TEXT, payload TEXT NOT NULL, signature TEXT NOT NULL, expires REAL NOT NULL, PRIMARY KEY(kind,id))')
            db.execute('DELETE FROM records WHERE expires < ?', (time.time(),))
        os.chmod(self.database, 0o600)

    def connect(self):
        connection=sqlite3.connect(str(self.database), timeout=5)
        connection.execute('PRAGMA secure_delete=ON')
        return connection

    def put(self, kind, record_id, payload, ttl, *, expires_at=None):
        expires = time.time() + ttl if expires_at is None else expires_at
        envelope = canonical({'kind': kind, 'id': record_id, 'payload': payload, 'expires': expires})
        signature = hmac.new(self.key, envelope.encode(), hashlib.sha256).hexdigest()
        with self.connect() as db:
            db.execute('DELETE FROM records WHERE expires < ?', (time.time(),))
            db.execute('INSERT OR REPLACE INTO records VALUES(?,?,?,?,?)', (kind, record_id, canonical(payload), signature, expires))

    def get(self, kind, record_id):
        started = time.monotonic()
        outcome = 'errors'
        try:
            payload = self._get(kind, record_id)
            outcome = 'hits' if payload is not None else 'misses'
            return payload
        finally:
            if self.measurements is not None:
                self.measurements.record(kind, outcome, (time.monotonic()-started)*1000)

    def _get(self, kind, record_id):
        with self.connect() as db:
            row = db.execute('SELECT payload, signature, expires FROM records WHERE kind=? AND id=?', (kind,record_id)).fetchone()
        if not row or row[2] < time.time():
            return None
        payload = json.loads(row[0])
        envelope = canonical({'kind':kind,'id':record_id,'payload':payload,'expires':row[2]})
        expected = hmac.new(self.key, envelope.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, row[1]):
            raise ValueError('Stored record failed authentication')
        return payload
