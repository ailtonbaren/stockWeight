"""Material profiles and durable ownership of each measurement context."""
from __future__ import annotations

import json
import unicodedata
import uuid


class Materials:
    def __init__(self, db, scope, *, legacy_unit=None, legacy_minimum=50):
        self.db, self.scope = db, scope
        db.executescript("""
            CREATE TABLE IF NOT EXISTS materials (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, name TEXT NOT NULL,
                name_key TEXT NOT NULL, unit_g REAL, minimum INTEGER NOT NULL,
                UNIQUE(scope, name_key)
            );
            CREATE TABLE IF NOT EXISTS material_runs (
                id TEXT PRIMARY KEY, scope TEXT NOT NULL, material_id TEXT NOT NULL,
                session_id TEXT NOT NULL, phase TEXT NOT NULL
            );
        """)
        for table in ("events", "readings"):
            columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            for column in ("material_id", "run_id"):
                if column not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            db.execute(f"CREATE INDEX IF NOT EXISTS {table}_material ON {table}(material_id, id)")
        if not self.list():
            default = self.create("Parafusos", unit_g=legacy_unit, minimum=legacy_minimum)
            # Previous versions monitored only screws. Keep old rows rather than
            # deleting history or pretending they belong to a newly created item.
            for table in ("events", "readings"):
                if scope == "local_simulator":
                    db.execute(f"UPDATE {table} SET material_id = ? WHERE material_id IS NULL AND substr(session_id, 1, 5) != 'mqtt:'", (default["id"],))
                else:
                    prefix = scope + ":"
                    db.execute(f"UPDATE {table} SET material_id = ? WHERE material_id IS NULL AND substr(session_id, 1, ?) = ?", (default["id"], len(prefix), prefix))
        row = db.execute("SELECT value FROM metadata WHERE key = ?", ("workflow:" + scope,)).fetchone()
        self.current = json.loads(row[0]) if row else {"material_id": self.list()[0]["id"], "context_id": None}
        db.commit()

    def list(self):
        return [dict(row) for row in self.db.execute("SELECT id, name, unit_g, minimum FROM materials WHERE scope = ? ORDER BY rowid", (self.scope,))]

    def get(self, material_id=None):
        material_id = material_id or self.current["material_id"]
        row = self.db.execute("SELECT id, name, unit_g, minimum FROM materials WHERE id = ? AND scope = ?", (material_id, self.scope)).fetchone()
        if row is None:
            raise ValueError("Material não encontrado.")
        return dict(row)

    def create(self, name, *, unit_g=None, minimum=50):
        if not isinstance(name, str):
            raise ValueError("Informe o nome do material.")
        name = unicodedata.normalize("NFKC", name).strip()
        if not 1 <= len(name) <= 80 or not all(c.isprintable() for c in name):
            raise ValueError("Use um nome de material entre 1 e 80 caracteres.")
        if self.db.execute("SELECT 1 FROM materials WHERE scope = ? AND name_key = ?", (self.scope, name.casefold())).fetchone():
            raise ValueError("Já existe um material com esse nome.")
        material_id = uuid.uuid4().hex
        self.db.execute("INSERT INTO materials VALUES (?, ?, ?, ?, ?, ?)", (material_id, self.scope, name, name.casefold(), unit_g, minimum))
        return self.get(material_id)

    def context(self, context_id):
        if not context_id:
            return None
        row = self.db.execute("SELECT * FROM material_runs WHERE id = ? AND scope = ?", (context_id, self.scope)).fetchone()
        return dict(row) if row else None

    def select(self, context_id, material_id, session_id, phase):
        self.get(material_id)
        self.db.execute("INSERT INTO material_runs VALUES (?, ?, ?, ?, ?)", (context_id, self.scope, material_id, session_id, phase))
        self.current = {"material_id": material_id, "context_id": context_id}
        self.db.execute("INSERT INTO metadata VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("workflow:" + self.scope, json.dumps(self.current)))
        self.db.commit()

    def save_settings(self, material_id, *, unit_g=None, minimum=None):
        if unit_g is not None:
            self.db.execute("UPDATE materials SET unit_g = ? WHERE id = ? AND scope = ?", (unit_g, material_id, self.scope))
        if minimum is not None:
            self.db.execute("UPDATE materials SET minimum = ? WHERE id = ? AND scope = ?", (minimum, material_id, self.scope))
        self.db.commit()
