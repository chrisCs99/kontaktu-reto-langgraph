"""Estado persistente entre invocaciones (SQLite local, sin red ni servidor).

Cada `python run.py evento.json` es un proceso nuevo (enunciado, seccion 2): nada
sobrevive en memoria. Lo que si tiene que sobrevivir -- intentos por lead, recordatorios
programados, bajas registradas, historial de llamadas cortadas -- vive aqui.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional


class KontaktuStore:
    def __init__(self, path: str | Path = "estado/kontaktu.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None)  # transacciones explicitas
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._crear_tablas()

    def _crear_tablas(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS decisiones_emitidas (
                idempotency_key TEXT PRIMARY KEY,
                event_id         TEXT NOT NULL,
                call_id          TEXT,
                etiqueta         TEXT NOT NULL,
                motivo           TEXT NOT NULL,
                confianza        REAL NOT NULL,
                ordenes_json     TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS leads (
                contact_id            TEXT PRIMARY KEY,
                intentos_voz          INTEGER NOT NULL DEFAULT 0,
                no_contactar          INTEGER NOT NULL DEFAULT 0,
                whatsapp_rechazado    INTEGER NOT NULL DEFAULT 0,
                canal_respaldo_usado  INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS historial_llamadas (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                contact_id   TEXT NOT NULL,
                event_id     TEXT NOT NULL,
                etiqueta     TEXT NOT NULL,
                occurred_at  TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recordatorios (
                reminder_id  TEXT PRIMARY KEY,
                contact_id   TEXT NOT NULL,
                canal        TEXT NOT NULL,
                estado       TEXT NOT NULL DEFAULT 'activo'
            );
            """
        )

    # -- transacciones -----------------------------------------------------
    def begin(self) -> None:
        self.conn.execute("BEGIN")

    def commit(self) -> None:
        self.conn.execute("COMMIT")

    def rollback(self) -> None:
        self.conn.execute("ROLLBACK")

    def close(self) -> None:
        self.conn.close()

    # -- dedupe / reentregas (R5) -------------------------------------------
    def decision_previa(self, idempotency_key: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM decisiones_emitidas WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        if row is None:
            return None
        return {
            "event_id": row["event_id"],
            "call_id": row["call_id"],
            "etiqueta": row["etiqueta"],
            "motivo": row["motivo"],
            "confianza": row["confianza"],
            "ordenes": json.loads(row["ordenes_json"]),
        }

    def guardar_decision(
        self, idempotency_key: str, event_id: str, call_id: Optional[str],
        etiqueta: str, motivo: str, confianza: float, ordenes: list[str],
    ) -> None:
        self.conn.execute(
            """INSERT INTO decisiones_emitidas
               (idempotency_key, event_id, call_id, etiqueta, motivo, confianza, ordenes_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (idempotency_key, event_id, call_id, etiqueta, motivo, confianza, json.dumps(ordenes)),
        )

    # -- leads (R4, N2, N1) --------------------------------------------------
    def lead_obtener(self, contact_id: str) -> dict:
        self.conn.execute("INSERT OR IGNORE INTO leads (contact_id) VALUES (?)", (contact_id,))
        row = self.conn.execute("SELECT * FROM leads WHERE contact_id = ?", (contact_id,)).fetchone()
        return dict(row)

    def lead_incrementar_intento(self, contact_id: str) -> int:
        self.lead_obtener(contact_id)
        self.conn.execute(
            "UPDATE leads SET intentos_voz = intentos_voz + 1 WHERE contact_id = ?", (contact_id,)
        )
        return self.conn.execute(
            "SELECT intentos_voz FROM leads WHERE contact_id = ?", (contact_id,)
        ).fetchone()["intentos_voz"]

    def lead_marcar_no_contactar(self, contact_id: str) -> None:
        self.lead_obtener(contact_id)
        self.conn.execute("UPDATE leads SET no_contactar = 1 WHERE contact_id = ?", (contact_id,))

    def lead_marcar_whatsapp_rechazado(self, contact_id: str) -> None:
        self.lead_obtener(contact_id)
        self.conn.execute("UPDATE leads SET whatsapp_rechazado = 1 WHERE contact_id = ?", (contact_id,))

    def lead_marcar_canal_respaldo_usado(self, contact_id: str) -> None:
        self.lead_obtener(contact_id)
        self.conn.execute("UPDATE leads SET canal_respaldo_usado = 1 WHERE contact_id = ?", (contact_id,))

    # -- historial de llamadas (N4: segunda cortada/visita_sin_confirmar) ---
    def historial_agregar(self, contact_id: str, event_id: str, etiqueta: str, occurred_at: str) -> None:
        self.conn.execute(
            "INSERT INTO historial_llamadas (contact_id, event_id, etiqueta, occurred_at) VALUES (?, ?, ?, ?)",
            (contact_id, event_id, etiqueta, occurred_at),
        )

    def historial_contar_etiquetas(self, contact_id: str, etiquetas: tuple[str, ...]) -> int:
        placeholders = ",".join("?" for _ in etiquetas)
        row = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM historial_llamadas WHERE contact_id = ? AND etiqueta IN ({placeholders})",
            (contact_id, *etiquetas),
        ).fetchone()
        return row["n"]

    # -- recordatorios (R7) ---------------------------------------------------
    def recordatorio_crear(self, reminder_id: str, contact_id: str, canal: str) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO recordatorios (reminder_id, contact_id, canal, estado) VALUES (?, ?, ?, 'activo')",
            (reminder_id, contact_id, canal),
        )

    def recordatorios_activos(self, contact_id: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT reminder_id FROM recordatorios WHERE contact_id = ? AND estado = 'activo'",
            (contact_id,),
        ).fetchall()
        return [r["reminder_id"] for r in rows]

    def recordatorio_cancelar(self, reminder_id: str) -> None:
        self.conn.execute(
            "UPDATE recordatorios SET estado = 'cancelado' WHERE reminder_id = ?", (reminder_id,)
        )
