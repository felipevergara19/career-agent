"""API y Dashboard web local para Career Agent (PostgreSQL + Neo4j GraphRAG + IA)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"
sys.path.insert(0, str(ROOT / "scripts"))

import evaluador_ia  # noqa: E402
import perfil_builder  # noqa: E402

app = FastAPI(title="Career Agent", version="2.0.0")

_pipeline_lock = threading.Lock()
_pipeline_state = {
    "running": False,
    "last_exit": None,
    "logs": [],
}


class MoverPayload(BaseModel):
    estado: str
    nota: str | None = None
    motivo: str | None = None
    forzar: bool = True


class PipelinePayload(BaseModel):
    foco: str = ""
    sin_busqueda: bool = False
    max_eval: int = 12


class AnalizarUrlPayload(BaseModel):
    url: str
    guardar: bool = True


class ConfigIAPayload(BaseModel):
    AI_PROVIDER: str | None = None
    OPENROUTER_API_KEY: str | None = None
    OPENROUTER_MODEL: str | None = None
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    ANTHROPIC_MODEL: str | None = None
    OPENAI_API_KEY: str | None = None
    OPENAI_MODEL: str | None = None
    OLLAMA_URL: str | None = None
    OLLAMA_MODEL: str | None = None
    roles: dict | None = None


class PerfilUrlPayload(BaseModel):
    url: str
    tipo: str = "auto"


class PerfilGuardarPayload(BaseModel):
    datos_personales: dict | None = None
    titular_principal: str | None = None
    resumen_profesional: str | None = None
    historia_libre: str | None = None
    skills: dict | None = None
    experiencias: list | None = None
    impact_log: list | None = None
    educacion: list | None = None
    certificaciones: list | None = None
    preferencias: dict | None = None


class PerfilMarkdownPayload(BaseModel):
    perfil_md: str | None = None
    preferencias_md: str | None = None


class GenerarCvBasePayload(BaseModel):
    idioma: str = "es"
    angulo: str = "base"
    usar_ia: bool = False


def _mask_key(val: str | None) -> str:
    if not val:
        return ""
    if len(val) <= 10:
        return "••••"
    return f"{val[:5]}...{val[-4:]}"


def _run_py(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / script), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def _sync_dbs_best_effort() -> None:
    if os.environ.get("DATABASE_URL") or os.environ.get("NEO4J_URI"):
        _run_py("db_sync.py", "--all")


def _neo4j_driver():
    neo_uri = os.environ.get("NEO4J_URI")
    if not neo_uri:
        return None
    from neo4j import GraphDatabase

    auth_raw = os.environ.get("NEO4J_AUTH", "neo4j/career_neo4j_pass")
    user, pwd = auth_raw.split("/", 1) if "/" in auth_raw else ("neo4j", auth_raw)
    return GraphDatabase.driver(neo_uri, auth=(user, pwd))


@app.get("/", response_class=HTMLResponse)
def index():
    html_path = STATIC_DIR / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Dashboard no encontrado")
    return html_path.read_text(encoding="utf-8")


@app.get("/api/health")
def health():
    pg_ok = False
    neo_ok = False
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            import psycopg

            with psycopg.connect(db_url, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            pg_ok = True
        except Exception:
            pg_ok = False

    try:
        driver = _neo4j_driver()
        if driver:
            driver.verify_connectivity()
            driver.close()
            neo_ok = True
    except Exception:
        neo_ok = False

    return {
        "status": "ok",
        "postgres": pg_ok,
        "neo4j": neo_ok,
        "ia": evaluador_ia.detectar_proveedor_ia(),
    }


@app.get("/api/ofertas")
def listar_ofertas():
    db_url = os.environ.get("DATABASE_URL")
    if db_url:
        try:
            import psycopg
            from psycopg.rows import dict_row

            with psycopg.connect(db_url, row_factory=dict_row, connect_timeout=2) as conn:
                rows = conn.execute(
                    "SELECT * FROM ofertas ORDER BY COALESCE(match_score, -1) DESC, fecha_captura DESC"
                ).fetchall()
                if rows:
                    return rows
        except Exception:
            pass

    res = _run_py("estado.py", "listar", "--json")
    if res.returncode != 0:
        raise HTTPException(status_code=500, detail=res.stderr)
    return json.loads(res.stdout or "[]")


@app.post("/api/ofertas/{oferta_id}/mover")
def mover_oferta(oferta_id: str, body: MoverPayload):
    # Si se arrastra directo a cv_generado y no tiene CV todavía, ensamblarlo automáticamente
    if body.estado == "cv_generado":
        ofertas = listar_ofertas()
        actual = next((o for o in ofertas if o["id"] == oferta_id), None)
        if actual and not actual.get("cv"):
            res_cv = _run_py("ensamblar_cv.py", "--oferta", oferta_id)
            if res_cv.returncode != 0:
                raise HTTPException(status_code=400, detail=res_cv.stderr or res_cv.stdout)
            _run_py("estado.py", "sincronizar")
            _sync_dbs_best_effort()
            return {"ok": True, "auto_cv": True, "stdout": res_cv.stdout}

    args = ["mover", oferta_id, body.estado]
    if body.forzar:
        args.append("--forzar")
    if body.nota:
        args.extend(["--nota", body.nota])
    if body.motivo:
        args.extend(["--motivo", body.motivo])
    res = _run_py("estado.py", *args)
    if res.returncode != 0:
        raise HTTPException(status_code=400, detail=res.stderr or res.stdout)
    _run_py("estado.py", "sincronizar")
    _sync_dbs_best_effort()
    return {"ok": True, "stdout": res.stdout}


@app.post("/api/ofertas/{oferta_id}/generar-cv")
def generar_cv_oferta(oferta_id: str):
    res = _run_py("ensamblar_cv.py", "--oferta", oferta_id)
    if res.returncode != 0 or "Traceback" in (res.stderr or "") or "PermissionError" in (res.stderr or ""):
        raise HTTPException(status_code=400, detail=res.stderr or res.stdout)
    res_sync = _run_py("estado.py", "sincronizar")
    if res_sync.returncode != 0:
        raise HTTPException(status_code=500, detail=res_sync.stderr or res_sync.stdout)
    _sync_dbs_best_effort()
    return {"ok": True, "stdout": res.stdout}


@app.get("/api/ofertas/{oferta_id}/grafo")
def oferta_grafo(oferta_id: str):
    """Consulta Neo4j GraphRAG para traer logros verificados que hacen match con esta oferta."""
    try:
        driver = _neo4j_driver()
        if driver:
            with driver.session() as session:
                logros = [
                    dict(r)
                    for r in session.run(
                        """
                        MATCH (o:Oferta {id: $oid})-[:REQUIERE_SKILL]->(s:Skill)<-[:DEMUESTRA_SKILL]-(l:Logro)
                        RETURN l.id AS id, l.titulo AS titulo, l.ahorro AS ahorro,
                               l.angulo AS angulo, collect(DISTINCT s.nombre) AS skills_match
                        ORDER BY size(skills_match) DESC LIMIT 6
                        """,
                        oid=f"oferta:{oferta_id}",
                    )
                ]
                faltantes = [
                    r["skill"]
                    for r in session.run(
                        """
                        MATCH (o:Oferta {id: $oid})-[:REQUIERE_SKILL]->(s:Skill)
                        WHERE NOT (:Logro)-[:DEMUESTRA_SKILL]->(s)
                        RETURN DISTINCT s.nombre AS skill LIMIT 10
                        """,
                        oid=f"oferta:{oferta_id}",
                    )
                ]
            driver.close()
            if logros or faltantes:
                return {"source": "neo4j", "logros": logros, "skills_sin_logro": faltantes}
    except Exception:
        pass

    # Fallback: buscar texto de la oferta en grafo local/Neo4j
    ofertas = listar_ofertas()
    actual = next((o for o in ofertas if o["id"] == oferta_id), None)
    if not actual:
        return {"source": "none", "logros": [], "skills_sin_logro": []}
    texto = f"{actual.get('cargo', '')} {' '.join(actual.get('keywords') or [])} {actual.get('notas', '')}"
    return {
        "source": "graphrag-text",
        "logros": evaluador_ia.consultar_grafo_contexto(texto),
        "skills_sin_logro": [],
    }


@app.get("/api/grafo/stats")
def grafo_stats():
    try:
        driver = _neo4j_driver()
        if driver:
            with driver.session() as session:
                n_nodes = session.run("MATCH (n:Entity) RETURN count(n) AS c").single()["c"]
                n_rels = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
                top_cubiertos = [
                    {"skill": r["skill"], "demanda": r["demanda"]}
                    for r in session.run(
                        """
                        MATCH (o:Oferta)-[:REQUIERE_SKILL]->(s:Skill)<-[:DEMUESTRA_SKILL]-(:Logro)
                        RETURN s.nombre AS skill, count(DISTINCT o) AS demanda
                        ORDER BY demanda DESC LIMIT 8
                        """
                    )
                ]
                top_brechas = [
                    {"skill": r["skill"], "demanda": r["demanda"]}
                    for r in session.run(
                        """
                        MATCH (o:Oferta)-[:REQUIERE_SKILL]->(s:Skill)
                        WHERE NOT (:Logro)-[:DEMUESTRA_SKILL]->(s)
                        RETURN s.nombre AS skill, count(DISTINCT o) AS demanda
                        ORDER BY demanda DESC LIMIT 8
                        """
                    )
                ]
            driver.close()
            return {
                "source": "neo4j",
                "nodos": n_nodes,
                "aristas": n_rels,
                "top_cubiertos": top_cubiertos,
                "top_brechas": top_brechas,
            }
    except Exception:
        pass

    grafo_path = ROOT / "postulaciones" / "grafo_conocimiento.json"
    if grafo_path.exists():
        data = json.loads(grafo_path.read_text(encoding="utf-8"))
        return {
            "source": "json",
            "nodos": data.get("total_nodos", 0),
            "aristas": data.get("total_aristas", 0),
            "top_cubiertos": [],
            "top_brechas": [],
        }
    return {"source": "none", "nodos": 0, "aristas": 0, "top_cubiertos": [], "top_brechas": []}


@app.post("/api/analizar-url")
def analizar_url(body: AnalizarUrlPayload):
    url = body.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Ingresa una URL válida (http:// o https://)")
    try:
        oferta = evaluador_ia.descargar_oferta_url(url)
        ev = evaluador_ia.evaluar_oferta(oferta)
        guardado = None
        if body.guardar:
            fpath, oid = evaluador_ia.guardar_ficha_evaluada(oferta, ev)
            guardado = {"path": str(fpath.relative_to(ROOT)) if fpath else None, "id": oid}
            _run_py("estado.py", "sincronizar")
            _sync_dbs_best_effort()
        return {
            "ok": True,
            "oferta": {k: v for k, v in oferta.items() if k != "descripcion"},
            "evaluacion": ev,
            "guardado": guardado,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error analizando URL: {exc}")


@app.post("/api/sync")
def sync_all():
    res_estado = _run_py("estado.py", "sincronizar")
    res_db = _run_py("db_sync.py", "--all")
    return {
        "ok": res_estado.returncode == 0,
        "estado": res_estado.stdout,
        "db": res_db.stdout + res_db.stderr,
    }


@app.post("/api/pipeline/run")
def run_pipeline(body: PipelinePayload):
    if _pipeline_state["running"]:
        raise HTTPException(status_code=409, detail="El pipeline ya está corriendo")

    def _log(line: str):
        _pipeline_state["logs"].append(line)
        if len(_pipeline_state["logs"]) > 200:
            _pipeline_state["logs"] = _pipeline_state["logs"][-200:]

    def _stream_cmd(cmd: list[str]) -> int:
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            _log(line.rstrip())
        return proc.wait()

    def _worker():
        with _pipeline_lock:
            _pipeline_state["running"] = True
            _pipeline_state["logs"] = []
            try:
                rc = 0
                if not body.sin_busqueda:
                    cmd = [
                        sys.executable,
                        str(ROOT / "scripts" / "rastrear_ats.py"),
                        "--buscar",
                        "--crear-ofertas",
                        "--max-eval",
                        str(body.max_eval),
                    ]
                    if body.foco:
                        cmd.extend(["--foco", body.foco])
                    _log("▶ Etapa 1: Rastreo multi-fuente + Filtros Chile/Remoto + Evaluación IA/GraphRAG...")
                    rc = _stream_cmd(cmd)

                _log("▶ Etapa 2: Sincronizando estado canónico, Postgres y Neo4j...")
                _stream_cmd([sys.executable, str(ROOT / "scripts" / "estado.py"), "sincronizar"])
                _stream_cmd([sys.executable, str(ROOT / "scripts" / "db_sync.py"), "--all"])
                _pipeline_state["last_exit"] = rc
                _log("✓ Corrida finalizada.")
            finally:
                _pipeline_state["running"] = False

    threading.Thread(target=_worker, daemon=True).start()
    return {"started": True}


@app.get("/api/pipeline/status")
def pipeline_status():
    return _pipeline_state


@app.get("/api/files/{rel_path:path}")
def serve_file(rel_path: str):
    target = (ROOT / rel_path).resolve()
    allowed_roots = [
        (ROOT / "cvs-generados").resolve(),
        (ROOT / "ofertas").resolve(),
        (ROOT / "perfil" / "documentos").resolve(),
    ]
    if not any(str(target).startswith(str(base)) for base in allowed_roots):
        raise HTTPException(status_code=404, detail="Archivo no disponible")

    # Si piden un PDF que aún no fue renderizado pero existe su .md, renderizarlo al vuelo
    if target.suffix.lower() == ".pdf" and not target.is_file():
        md_sibling = target.with_suffix(".md")
        if md_sibling.is_file():
            render_script = ROOT / ".agents" / "skills" / "adaptador-cv" / "scripts" / "render_cv.py"
            subprocess.run([sys.executable, str(render_script), str(md_sibling)], cwd=ROOT, capture_output=True)
        # Fallback al HTML ATS si el PDF aún no existe
        if not target.is_file() and target.with_suffix(".html").is_file():
            return FileResponse(target.with_suffix(".html"))

    if not target.is_file():
        raise HTTPException(status_code=404, detail="Archivo no disponible")
    return FileResponse(target)


@app.get("/api/config/ia")
def get_config_ia():
    evaluador_ia.cargar_config_ia_local()
    prov = evaluador_ia.detectar_proveedor_ia()
    roles_cfg = evaluador_ia.obtener_roles_config()
    roles_efectivos = {k: evaluador_ia.detectar_proveedor_ia(rol=k) for k in roles_cfg}
    return {
        "activo": prov,
        "AI_PROVIDER": os.environ.get("AI_PROVIDER", ""),
        "OPENROUTER_API_KEY": _mask_key(os.environ.get("OPENROUTER_API_KEY")),
        "OPENROUTER_MODEL": os.environ.get("OPENROUTER_MODEL", "google/gemini-2.5-flash"),
        "GEMINI_API_KEY": _mask_key(os.environ.get("GEMINI_API_KEY")),
        "GEMINI_MODEL": os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        "ANTHROPIC_API_KEY": _mask_key(os.environ.get("ANTHROPIC_API_KEY")),
        "ANTHROPIC_MODEL": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5"),
        "OPENAI_API_KEY": _mask_key(os.environ.get("OPENAI_API_KEY")),
        "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        "OLLAMA_URL": os.environ.get("OLLAMA_URL", ""),
        "OLLAMA_MODEL": os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
        "roles": roles_cfg,
        "roles_efectivos": roles_efectivos,
        "has_perfil": (ROOT / "perfil" / "perfil-maestro.md").is_file(),
        "has_preferencias": (ROOT / "perfil" / "preferencias.md").is_file(),
    }


@app.post("/api/config/ia")
def save_config_ia(body: ConfigIAPayload):
    evaluador_ia.guardar_config_ia_local(body.model_dump(exclude_none=True))
    roles_cfg = evaluador_ia.obtener_roles_config()
    return {
        "ok": True,
        "activo": evaluador_ia.detectar_proveedor_ia(),
        "roles_efectivos": {k: evaluador_ia.detectar_proveedor_ia(rol=k) for k in roles_cfg},
    }


@app.post("/api/config/ia/test")
def test_config_ia(rol: str | None = None):
    return evaluador_ia.probar_conexion_ia(rol=rol)


# ------------------------------------------- Onboarding, Perfil & Documentos
@app.get("/api/perfil")
def get_perfil():
    perfil = perfil_builder.cargar_perfil_estructurado()
    docs = perfil_builder.listar_documentos()
    historicas = perfil_builder.detectar_fuentes_historicas()
    completitud = perfil_builder.calcular_completitud(perfil, docs)
    perfil_md = (
        perfil_builder.PERFIL_MD_PATH.read_text(encoding="utf-8", errors="ignore")
        if perfil_builder.PERFIL_MD_PATH.is_file()
        else ""
    )
    pref_md = (
        perfil_builder.PREF_MD_PATH.read_text(encoding="utf-8", errors="ignore")
        if perfil_builder.PREF_MD_PATH.is_file()
        else ""
    )
    return {
        "perfil": perfil,
        "documentos": docs,
        "fuentes_historicas": historicas,
        "completitud": completitud,
        "has_perfil_md": perfil_builder.PERFIL_MD_PATH.is_file(),
        "has_preferencias_md": perfil_builder.PREF_MD_PATH.is_file(),
        "perfil_md": perfil_md,
        "preferencias_md": pref_md,
    }


@app.post("/api/perfil/documentos")
async def upload_documentos_perfil(
    files: list[UploadFile] = File(...),
    categoria: str = Form("auto"),
    usar_como_cv_base: bool = Form(False),
):
    guardados = []
    errores = []
    for f in files:
        try:
            content = await f.read()
            entry = perfil_builder.guardar_documento(
                content=content,
                filename=f.filename or "documento.txt",
                categoria=categoria,
                usar_como_cv_base=usar_como_cv_base and len(guardados) == 0,
            )
            guardados.append(entry)
        except Exception as exc:
            errores.append({"archivo": f.filename, "error": str(exc)})
    if not guardados and errores:
        raise HTTPException(status_code=400, detail=errores[0]["error"])
    return {"ok": True, "documentos": guardados, "errores": errores}


@app.delete("/api/perfil/documentos/{doc_id}")
def delete_documento_perfil(doc_id: str):
    if not perfil_builder.eliminar_documento(doc_id):
        raise HTTPException(status_code=404, detail="Documento no encontrado")
    return {"ok": True}


@app.post("/api/perfil/documentos/{doc_id}/usar-cv-base")
def set_documento_cv_base(doc_id: str):
    try:
        cv_base = perfil_builder.activar_documento_como_cv_base(doc_id)
        return {"ok": True, "cv_base": cv_base}
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/api/perfil/url")
def importar_url_perfil(body: PerfilUrlPayload):
    try:
        res = perfil_builder.importar_url_perfil(body.url, tipo=body.tipo)
        return {"ok": True, **res}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error importando URL: {exc}")


@app.post("/api/perfil/guardar")
def guardar_perfil(body: PerfilGuardarPayload):
    actual = perfil_builder.cargar_perfil_estructurado()
    updates = body.model_dump(exclude_none=True)
    merged = perfil_builder.guardar_perfil_estructurado({**actual, **updates}, regenerar_md=True)
    _run_py("grafo.py", "--construir")
    _sync_dbs_best_effort()
    return {
        "ok": True,
        "perfil": merged,
        "completitud": perfil_builder.calcular_completitud(merged),
    }


@app.post("/api/perfil/sintetizar")
def sintetizar_perfil():
    try:
        res = perfil_builder.sintetizar_perfil_con_ia()
        _run_py("grafo.py", "--construir")
        _sync_dbs_best_effort()
        return {
            "ok": True,
            **res,
            "completitud": perfil_builder.calcular_completitud(res["perfil"]),
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error sintetizando perfil: {exc}")


@app.post("/api/perfil/generar-cv-base")
def generar_cv_base(body: GenerarCvBasePayload):
    args = ["ensamblar_cv.py", "--base", "--idioma", body.idioma, "--angulo", body.angulo]
    if body.usar_ia:
        args.append("--usar-ia")
    res = _run_py(*args)
    if res.returncode != 0:
        raise HTTPException(status_code=400, detail=res.stderr or res.stdout)
    perfil = perfil_builder.cargar_perfil_estructurado()
    return {"ok": True, "cv_base": perfil.get("cv_base"), "stdout": res.stdout}


@app.post("/api/perfil/markdown")
def guardar_perfil_markdown(body: PerfilMarkdownPayload):
    perfil_builder.PERFIL_DIR.mkdir(parents=True, exist_ok=True)
    if body.perfil_md is not None:
        perfil_builder.PERFIL_MD_PATH.write_text(body.perfil_md, encoding="utf-8")
    if body.preferencias_md is not None:
        perfil_builder.PREF_MD_PATH.write_text(body.preferencias_md, encoding="utf-8")
    _run_py("grafo.py", "--construir")
    _sync_dbs_best_effort()
    return {"ok": True}
