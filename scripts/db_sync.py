#!/usr/bin/env python3
"""Sincronizador de estado hacia PostgreSQL (relacional) y Neo4j (grafo de conocimiento).

Lee el estado canónico generado por `estado.py` y `grafo.py` y lo persiste en las
bases de datos de Docker Compose para consultas rápidas, UI y agentes IA (GraphRAG).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
GRAFO_JSON = ROOT / "postulaciones" / "grafo_conocimiento.json"


def _parse_neo4j_auth(raw: str) -> tuple[str, str]:
    if "/" in raw:
        user, pwd = raw.split("/", 1)
        return user, pwd
    return "neo4j", raw


def _leer_extras_frontmatter(rel_archivo: str | None) -> dict:
    if not rel_archivo:
        return {}
    p = ROOT / rel_archivo
    if not p.is_file():
        return {}
    txt = p.read_text(encoding="utf-8", errors="ignore")
    if not txt.startswith("---"):
        return {}
    partes = txt.split("---", 2)
    if len(partes) < 3:
        return {}
    try:
        fm = yaml.safe_load(partes[1]) or {}
        return {
            "desglose": fm.get("desglose") or {},
            "logros_grafo": fm.get("logros_grafo") or [],
            "resumen_ia": fm.get("resumen_ia") or "",
            "evaluado_por": fm.get("evaluado_por") or "",
        }
    except Exception:
        return {}


def cargar_ofertas_json() -> list[dict]:
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "estado.py"), "listar", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    ofertas = json.loads(res.stdout or "[]")
    for o in ofertas:
        extras = _leer_extras_frontmatter(o.get("archivo"))
        o.update(extras)
    return ofertas


def sync_postgres(database_url: str) -> int:
    import psycopg

    ofertas = cargar_ofertas_json()
    ids_vigentes = [o["id"] for o in ofertas]
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS ofertas (
                    id TEXT PRIMARY KEY,
                    empresa TEXT NOT NULL,
                    cargo TEXT NOT NULL,
                    url TEXT,
                    portal TEXT,
                    fecha_captura TEXT,
                    modalidad TEXT,
                    idioma TEXT,
                    salario TEXT,
                    match_score INTEGER,
                    prioridad TEXT,
                    estado TEXT NOT NULL,
                    angulo TEXT,
                    cv TEXT,
                    motivo_descarte TEXT,
                    keywords JSONB DEFAULT '[]'::jsonb,
                    brechas JSONB DEFAULT '[]'::jsonb,
                    desglose JSONB DEFAULT '{}'::jsonb,
                    logros_grafo JSONB DEFAULT '[]'::jsonb,
                    resumen_ia TEXT DEFAULT '',
                    evaluado_por TEXT DEFAULT '',
                    notas TEXT,
                    fecha_postulacion TEXT,
                    actualizado TEXT,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                ALTER TABLE ofertas ADD COLUMN IF NOT EXISTS desglose JSONB DEFAULT '{}'::jsonb;
                ALTER TABLE ofertas ADD COLUMN IF NOT EXISTS logros_grafo JSONB DEFAULT '[]'::jsonb;
                ALTER TABLE ofertas ADD COLUMN IF NOT EXISTS resumen_ia TEXT DEFAULT '';
                ALTER TABLE ofertas ADD COLUMN IF NOT EXISTS evaluado_por TEXT DEFAULT '';
                CREATE TABLE IF NOT EXISTS pipeline_runs (
                    id SERIAL PRIMARY KEY,
                    comando TEXT NOT NULL,
                    estado TEXT NOT NULL,
                    detalle TEXT,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                );
                CREATE TABLE IF NOT EXISTS perfil_usuario (
                    id TEXT PRIMARY KEY,
                    nombre TEXT,
                    titular TEXT,
                    datos JSONB DEFAULT '{}'::jsonb,
                    updated_at TIMESTAMPTZ DEFAULT NOW()
                );
                """
            )
            # Sincronizar perfil estructurado si existe
            sp = ROOT / "perfil" / "perfil-estructurado.json"
            if sp.is_file():
                try:
                    pdata = json.loads(sp.read_text(encoding="utf-8"))
                    cur.execute(
                        """
                        INSERT INTO perfil_usuario (id, nombre, titular, datos, updated_at)
                        VALUES ('principal', %s, %s, %s::jsonb, NOW())
                        ON CONFLICT (id) DO UPDATE SET
                            nombre = EXCLUDED.nombre,
                            titular = EXCLUDED.titular,
                            datos = EXCLUDED.datos,
                            updated_at = NOW();
                        """,
                        (
                            (pdata.get("datos_personales") or {}).get("nombre") or "",
                            pdata.get("titular_principal") or "",
                            json.dumps(pdata, ensure_ascii=False),
                        ),
                    )
                except Exception:
                    pass
            # Eliminar filas huérfanas que ya fueron borradas de ofertas/
            if ids_vigentes:
                cur.execute("DELETE FROM ofertas WHERE id <> ALL(%s)", (ids_vigentes,))
            for o in ofertas:
                cur.execute(
                    """
                    INSERT INTO ofertas (
                        id, empresa, cargo, url, portal, fecha_captura, modalidad, idioma,
                        salario, match_score, prioridad, estado, angulo, cv, motivo_descarte,
                        keywords, brechas, desglose, logros_grafo, resumen_ia, evaluado_por,
                        notas, fecha_postulacion, actualizado, updated_at
                    ) VALUES (
                        %(id)s, %(empresa)s, %(cargo)s, %(url)s, %(portal)s, %(fecha_captura)s,
                        %(modalidad)s, %(idioma)s, %(salario)s, %(match_score)s, %(prioridad)s,
                        %(estado)s, %(angulo)s, %(cv)s, %(motivo_descarte)s,
                        %(keywords)s::jsonb, %(brechas)s::jsonb, %(desglose)s::jsonb,
                        %(logros_grafo)s::jsonb, %(resumen_ia)s, %(evaluado_por)s,
                        %(notas)s, %(fecha_postulacion)s, %(actualizado)s, NOW()
                    )
                    ON CONFLICT (id) DO UPDATE SET
                        empresa = EXCLUDED.empresa,
                        cargo = EXCLUDED.cargo,
                        url = EXCLUDED.url,
                        portal = EXCLUDED.portal,
                        modalidad = EXCLUDED.modalidad,
                        idioma = EXCLUDED.idioma,
                        salario = EXCLUDED.salario,
                        match_score = EXCLUDED.match_score,
                        prioridad = EXCLUDED.prioridad,
                        estado = EXCLUDED.estado,
                        angulo = EXCLUDED.angulo,
                        cv = EXCLUDED.cv,
                        motivo_descarte = EXCLUDED.motivo_descarte,
                        keywords = EXCLUDED.keywords,
                        brechas = EXCLUDED.brechas,
                        desglose = EXCLUDED.desglose,
                        logros_grafo = EXCLUDED.logros_grafo,
                        resumen_ia = EXCLUDED.resumen_ia,
                        evaluado_por = EXCLUDED.evaluado_por,
                        notas = EXCLUDED.notas,
                        fecha_postulacion = EXCLUDED.fecha_postulacion,
                        actualizado = EXCLUDED.actualizado,
                        updated_at = NOW();
                    """,
                    {
                        "id": o["id"],
                        "empresa": o.get("empresa") or "",
                        "cargo": o.get("cargo") or "",
                        "url": o.get("url"),
                        "portal": o.get("portal"),
                        "fecha_captura": str(o.get("fecha_captura") or ""),
                        "modalidad": o.get("modalidad"),
                        "idioma": o.get("idioma"),
                        "salario": o.get("salario"),
                        "match_score": o.get("match_score"),
                        "prioridad": o.get("prioridad"),
                        "estado": o.get("estado") or "evaluada",
                        "angulo": o.get("angulo"),
                        "cv": o.get("cv"),
                        "motivo_descarte": o.get("motivo_descarte"),
                        "keywords": json.dumps(o.get("keywords") or []),
                        "brechas": json.dumps(o.get("brechas") or []),
                        "desglose": json.dumps(o.get("desglose") or {}),
                        "logros_grafo": json.dumps(o.get("logros_grafo") or []),
                        "resumen_ia": o.get("resumen_ia") or "",
                        "evaluado_por": o.get("evaluado_por") or "",
                        "notas": o.get("notas") or "",
                        "fecha_postulacion": str(o.get("fecha_postulacion") or "") or None,
                        "actualizado": str(o.get("actualizado") or ""),
                    },
                )
        conn.commit()
    return len(ofertas)


def sync_neo4j(uri: str, auth_raw: str) -> tuple[int, int]:
    from neo4j import GraphDatabase

    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "grafo.py"), "--construir"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )

    data = json.loads(GRAFO_JSON.read_text(encoding="utf-8"))
    nodos_raw = list(data.get("nodos", {}).values())
    aristas_raw = data.get("aristas", [])
    ids_vigentes = [n["id"] for n in nodos_raw]

    by_type: dict[str, list[dict]] = {}
    for n in nodos_raw:
        t = (n.get("type") or "Entity").capitalize()
        safe_props = {}
        for k, v in n.items():
            if isinstance(v, (str, int, float, bool)) or (
                isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v)
            ):
                safe_props[k] = v
        by_type.setdefault(t, []).append({"id": n["id"], "props": safe_props})

    by_rel: dict[str, list[dict]] = {}
    for e in aristas_raw:
        rel = "".join(c if c.isalnum() or c == "_" else "_" for c in (e.get("rel") or "RELACIONADO")).upper()
        by_rel.setdefault(rel, []).append(
            {"from": e["from"], "to": e["to"], "weight": float(e.get("weight", 1.0))}
        )

    user, pwd = _parse_neo4j_auth(auth_raw)
    driver = GraphDatabase.driver(uri, auth=(user, pwd))
    try:
        with driver.session() as session:
            session.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE")
            # Limpiar ofertas borradas
            session.run("MATCH (n:Entity) WHERE NOT n.id IN $ids DETACH DELETE n", ids=ids_vigentes)
            for label, rows in by_type.items():
                safe_label = "".join(c for c in label if c.isalnum()) or "Entity"
                session.run(
                    f"""
                    UNWIND $rows AS row
                    MERGE (n:Entity {{id: row.id}})
                    SET n:{safe_label}
                    SET n += row.props
                    """,
                    rows=rows,
                )
            for rel, rows in by_rel.items():
                session.run(
                    f"""
                    UNWIND $rows AS row
                    MATCH (a:Entity {{id: row.from}})
                    MATCH (b:Entity {{id: row.to}})
                    MERGE (a)-[r:{rel}]->(b)
                    SET r.weight = row.weight
                    """,
                    rows=rows,
                )
    finally:
        driver.close()
    return len(nodos_raw), len(aristas_raw)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--postgres", action="store_true", help="Sincroniza ofertas hacia PostgreSQL")
    p.add_argument("--neo4j", action="store_true", help="Sincroniza grafo de conocimiento hacia Neo4j")
    p.add_argument("--all", action="store_true", help="Sincroniza ambas bases de datos")
    args = p.parse_args()

    run_pg = args.all or args.postgres or (not args.postgres and not args.neo4j)
    run_neo = args.all or args.neo4j or (not args.postgres and not args.neo4j)

    if run_pg:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            print("DATABASE_URL no definido; omitiendo PostgreSQL.")
        else:
            total = sync_postgres(db_url)
            print(f"✓ PostgreSQL sincronizado: {total} ofertas")

    if run_neo:
        uri = os.environ.get("NEO4J_URI")
        auth = os.environ.get("NEO4J_AUTH", "neo4j/career_neo4j_pass")
        if not uri:
            print("NEO4J_URI no definido; omitiendo Neo4j.")
        else:
            nodos, aristas = sync_neo4j(uri, auth)
            print(f"✓ Neo4j sincronizado: {nodos} nodos, {aristas} aristas")

    return 0


if __name__ == "__main__":
    sys.exit(main())
