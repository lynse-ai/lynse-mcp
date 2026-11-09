"""Durable, encrypted OAuth state for a single ECS deployment."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from cryptography.fernet import Fernet


def fingerprint(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class OAuthStore:
    def __init__(self, path: str, encryption_key: str):
        self.cipher = Fernet(encryption_key.encode())
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS records "
                       "(kind TEXT, id TEXT, payload BLOB NOT NULL, expires REAL NOT NULL, "
                       "PRIMARY KEY(kind, id))")

    @contextmanager
    def transaction(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def read(self, db, kind: str, key: str):
        row = db.execute("SELECT payload, expires FROM records WHERE kind=? AND id=?",
                         (kind, key)).fetchone()
        if row is None or row[1] <= time.time():
            return None
        envelope = json.loads(self.cipher.decrypt(row[0]))
        if envelope["kind"] != kind or envelope["id"] != key:
            raise ValueError("OAuth storage integrity check failed")
        return envelope["value"]

    def scan(self, db, kind: str):
        """Yield (id, value) for every stored record of a kind, including
        records whose business lifetime ended; row expiry is only retention."""
        for key, blob in db.execute("SELECT id, payload FROM records WHERE kind=?", (kind,)):
            envelope = json.loads(self.cipher.decrypt(blob))
            if envelope["kind"] == kind and envelope["id"] == key:
                yield key, envelope["value"]

    def write(self, db, kind: str, key: str, value: dict, expires: float):
        payload = self.cipher.encrypt(json.dumps(
            {"kind": kind, "id": key, "value": value}, separators=(",", ":")
        ).encode())
        db.execute("INSERT OR REPLACE INTO records VALUES (?, ?, ?, ?)",
                   (kind, key, payload, expires))

    def get(self, kind: str, key: str):
        with self.transaction() as db:
            return self.read(db, kind, key)

    def put(self, kind: str, key: str, value: dict, expires: float):
        with self.transaction() as db:
            db.execute("DELETE FROM records WHERE expires <= ?", (time.time(),))
            count = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            if count >= 50000:
                raise ValueError("OAuth storage capacity reached")
            self.write(db, kind, key, value, expires)

    @staticmethod
    def delete(db, kind: str, key: str):
        db.execute("DELETE FROM records WHERE kind=? AND id=?", (kind, key))

    def rate_limit(self, key: str, maximum: int, window: int):
        bucket = str(int(time.time()) // window)
        with self.transaction() as db:
            db.execute("DELETE FROM records WHERE expires <= ?", (time.time(),))
            record = self.read(db, "rate", key + bucket) or {"count": 0}
            record["count"] += 1
            self.write(db, "rate", key + bucket, record, time.time() + window)
            return record["count"] > maximum
