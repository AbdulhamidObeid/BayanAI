"""Private durable question queue; no deadline on accepted work.

Publication gates remain in the orchestrator (see guide 10). Only complete
pipeline results are stored. Leases recover abandoned workers after restart;
ownership checks prevent a late worker overwriting a recovered result.
"""
import hashlib
import hmac
import json
import sqlite3
import time
import os
from src.core.integrity import AuthenticatedStore, canonical, fingerprint
from src.core.source_policy import ROOT


def execution_config():
    return json.loads((ROOT / 'configs/query_execution.json').read_text(encoding='utf-8'))


class QueryJobs:
    def __init__(self):
        private = AuthenticatedStore()
        self.key = private.key
        self.path = private.directory / 'query_jobs.db'
        self.config = execution_config()
        with self.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, input_hash TEXT NOT NULL, payload TEXT NOT NULL,
                signature TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL,
                ready REAL NOT NULL, lease REAL NOT NULL DEFAULT 0, owner TEXT,
                attempts INTEGER NOT NULL DEFAULT 0, expires REAL)''')
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(str(self.path), timeout=5)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA secure_delete=ON')
        return db

    def encode(self, job_id, value):
        payload = canonical(value)
        signature = hmac.new(self.key, (job_id + payload).encode(), hashlib.sha256).hexdigest()
        return payload, signature

    def decode(self, row):
        expected = hmac.new(self.key, (row['id'] + row['payload']).encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, row['signature']):
            raise ValueError('Question job failed integrity verification')
        return json.loads(row['payload'])

    def submit(self, token, request):
        # The browser's random token is a bearer credential; do not store it.
        job_id = fingerprint(token)
        digest = fingerprint(request)
        payload, signature = self.encode(job_id, {'request': request})
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT input_hash FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row:
                if row['input_hash'] != digest:
                    raise ValueError('Request token already belongs to another question')
                return job_id
            db.execute('''INSERT INTO jobs(id,input_hash,payload,signature,status,created,ready)
                VALUES(?,?,?,?,?,?,?)''', (job_id, digest, payload, signature, 'queued', now, now))
        return job_id

    def get(self, token):
        with self.connect() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (fingerprint(token),)).fetchone()
        if not row or (row['expires'] is not None and row['expires'] <= time.time()):
            return None
        return {**dict(row), 'value': self.decode(row)}

    def claim(self, owner, capacity):
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM jobs WHERE expires <= ?', (now,))
            active = db.execute("SELECT count(*) FROM jobs WHERE status='running' AND lease>?", (now,)).fetchone()[0]
            if active >= capacity:
                return None
            row = db.execute('''SELECT * FROM jobs WHERE
                (status IN ('queued','retrying') AND ready<=?) OR
                (status='running' AND lease<=?) ORDER BY ready,created LIMIT 1''', (now, now)).fetchone()
            if not row:
                return None
            try:
                value = self.decode(row)
            except ValueError:
                # Quarantine corrupted work so it cannot poison the queue.
                # Its owner receives an integrity error, never invented output.
                db.execute("UPDATE jobs SET status='invalid',expires=? WHERE id=?",
                    (now + self.config['result_retention_seconds'],row['id']))
                return None
            db.execute("UPDATE jobs SET status='running',owner=?,lease=?,attempts=attempts+1 WHERE id=?",
                       (owner, now + self.config['lease_seconds'], row['id']))
        return {**dict(row), 'value': value, 'attempts': row['attempts'] + 1}

    def heartbeat(self, job_id, owner):
        with self.connect() as db:
            return db.execute("UPDATE jobs SET lease=? WHERE id=? AND owner=? AND status='running'",
                (time.time() + self.config['lease_seconds'], job_id, owner)).rowcount == 1

    def finish(self, job_id, owner, result, status_code):
        payload, signature = self.encode(job_id, {'result': result, 'status_code': status_code})
        with self.connect() as db:
            return db.execute("""UPDATE jobs SET payload=?,signature=?,status='completed',
                owner=NULL,lease=0,expires=? WHERE id=? AND owner=? AND status='running'""",
                (payload, signature, time.time() + self.config['result_retention_seconds'], job_id, owner)).rowcount == 1

    def retry(self, job_id, owner, delay):
        with self.connect() as db:
            db.execute("""UPDATE jobs SET status='retrying',ready=?,owner=NULL,lease=0
                WHERE id=? AND owner=? AND status='running'""", (time.time() + delay, job_id, owner))
