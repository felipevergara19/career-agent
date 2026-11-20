#!/usr/bin/env python3
"""
Motor de Grafo de Conocimiento (Knowledge Graph) para Career Agent.
Conecta de forma relacional:
  (Perfil del Usuario) ──► (Logros Impact Log) ──► (Skills) ◄── (Requisitos Ofertas)
                                                                    │
                                                                    ▼
                                                             (Radar Brechas)

Lee dinámicamente `perfil/perfil-estructurado.json` (generado desde el Onboarding
Web / documentos subidos) o, en su defecto, `perfil/perfil-maestro.md`.
Totalmente determinista y costo 0 tokens de LLM.
"""

import argparse
import glob
import json
import os
import re
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GRAFO_JSON_PATH = os.path.join(ROOT, "postulaciones", "grafo_conocimiento.json")
STRUCTURED_PERFIL_PATH = os.path.join(ROOT, "perfil", "perfil-estructurado.json")
PERFIL_MD_PATH = os.path.join(ROOT, "perfil", "perfil-maestro.md")


def _slug(text: str, fallback: str = "item") -> str:
    s = (text or "").lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n")):
        s = s.replace(a, b)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:48] or fallback


def _cargar_perfil_y_logros() -> tuple[dict, list[dict], list[str]]:
    """Carga el perfil real del usuario, sus logros verificados y skills desde
    perfil/perfil-estructurado.json (o fallback desde perfil/perfil-maestro.md)."""
    if os.path.isfile(STRUCTURED_PERFIL_PATH):
        try:
            with open(STRUCTURED_PERFIL_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            dp = data.get("datos_personales") or {}
            nombre = (dp.get("nombre") or "Usuario").strip()
            perfil_node = {
                "id": f"perfil:{_slug(nombre, 'usuario')}",
                "nombre": nombre,
                "titulo": (data.get("titular_principal") or "Profesional").strip(),
                "certificaciones": [
                    c.get("nombre") for c in (data.get("certificaciones") or []) if isinstance(c, dict) and c.get("nombre")
                ],
                "idiomas": [
                    f"{i.get('idioma')} ({i.get('nivel', '')})".strip()
                    for i in (dp.get("idiomas") or [])
                    if isinstance(i, dict) and i.get("idioma")
                ],
            }
            skills_user = [
                str(s).strip().lower()
                for lvl in ("avanzado", "intermedio")
                for s in ((data.get("skills") or {}).get(lvl) or [])
                if str(s).strip()
            ]
            logros = []
            for idx, lg in enumerate(data.get("impact_log") or [], 1):
                if not isinstance(lg, dict) or not (lg.get("titulo") or "").strip():
                    continue
                lid = lg.get("id") or f"logro:{_slug(lg['titulo'], f'item-{idx}')}"
                if not lid.startswith("logro:"):
                    lid = f"logro:{_slug(lid)}"
                sks = [str(s).strip().lower() for s in (lg.get("skills") or []) if str(s).strip()]
                if not sks and skills_user:
                    # Vincular skills mencionados literalmente en el título/descripción
                    txt = f"{lg.get('titulo', '')} {lg.get('descripcion', '')}".lower()
                    sks = [s for s in skills_user if re.search(r"(?<![a-z0-9])" + re.escape(s) + r"(?![a-z0-9])", txt)]
                logros.append({
                    "id": lid,
                    "titulo": lg["titulo"].strip(),
                    "angulo": (lg.get("angulo") or "base").strip().lower(),
                    "ahorro": (lg.get("ahorro") or lg.get("metricas") or "Impacto verificado").strip(),
                    "skills": sks,
                    "metricas": (lg.get("metricas") or lg.get("ahorro") or "").strip(),
                    "descripcion": (lg.get("descripcion") or lg["titulo"]).strip(),
                })

            # Si no hay impact_log todavía pero sí experiencias con bullets, construir logros base desde bullets
            if not logros:
                for exp in data.get("experiencias") or []:
                    emp = exp.get("empresa") or "Experiencia"
                    for bi, bullet in enumerate((exp.get("bullets") or [])[:4], 1):
                        b_txt = str(bullet).strip()
                        if not b_txt:
                            continue
                        sks = [
                            s for s in skills_user
                            if re.search(r"(?<![a-z0-9])" + re.escape(s) + r"(?![a-z0-9])", b_txt.lower())
                        ]
                        logros.append({
                            "id": f"logro:{_slug(emp)}-{bi}",
                            "titulo": b_txt[:110],
                            "angulo": ((data.get("preferencias") or {}).get("areas") or [{}])[0].get("slug", "base"),
                            "ahorro": emp,
                            "skills": sks,
                            "metricas": b_txt[:180],
                            "descripcion": b_txt,
                        })
            return perfil_node, logros, list(dict.fromkeys(skills_user))
        except Exception:
            pass

    # Fallback: usar el parser completo de perfil_builder sobre perfil-maestro.md
    if os.path.isfile(PERFIL_MD_PATH):
        try:
            import sys
            scripts_dir = os.path.dirname(os.path.abspath(__file__))
            if scripts_dir not in sys.path:
                sys.path.insert(0, scripts_dir)
            import perfil_builder
            data = perfil_builder.cargar_perfil_estructurado()
            dp = data.get("datos_personales") or {}
            nombre = (dp.get("nombre") or "Usuario").strip()
            if nombre:
                perfil_node = {
                    "id": f"perfil:{_slug(nombre, 'usuario')}",
                    "nombre": nombre,
                    "titulo": (data.get("titular_principal") or "Perfil Profesional").strip(),
                    "certificaciones": [c.get("nombre") for c in (data.get("certificaciones") or []) if isinstance(c, dict) and c.get("nombre")],
                    "idiomas": [f"{i.get('idioma')} ({i.get('nivel', '')})".strip() for i in (dp.get("idiomas") or []) if isinstance(i, dict) and i.get("idioma")],
                }
                skills_user = [
                    str(s).strip().lower()
                    for lvl in ("avanzado", "intermedio")
                    for s in ((data.get("skills") or {}).get(lvl) or [])
                    if str(s).strip()
                ]
                return perfil_node, data.get("impact_log") or [], list(dict.fromkeys(skills_user))
        except Exception:
            pass

    return (
        {
            "id": "perfil:usuario",
            "nombre": "Perfil sin configurar",
            "titulo": "Configura tu perfil en Mi Perfil & Onboarding",
            "certificaciones": [],
            "idiomas": [],
        },
        [],
        [],
    )


class KnowledgeGraph:
    def __init__(self):
        self.nodes = {}
        self.edges = []
        self.perfil_node = {}
        self.logros = []

    def add_node(self, node_id, node_type, data):
        self.nodes[node_id] = {
            "id": node_id,
            "type": node_type,
            **data
        }

    def add_edge(self, from_id, to_id, rel_type, weight=1.0):
        self.edges.append({
            "from": from_id,
            "to": to_id,
            "rel": rel_type,
            "weight": weight
        })

    def build_from_repo(self):
        perfil_node, logros, skills_user = _cargar_perfil_y_logros()
        self.perfil_node = perfil_node
        self.logros = logros
        pid = perfil_node["id"]

        # 1. Nodo Perfil del Usuario
        self.add_node(pid, "perfil", {
            "nombre": perfil_node["nombre"],
            "titulo": perfil_node["titulo"],
            "certificaciones": perfil_node.get("certificaciones") or [],
            "idiomas": perfil_node.get("idiomas") or [],
        })

        # 1b. Skills declarados directamente en el perfil
        for sk in skills_user:
            sk_id = f"skill:{sk}"
            if sk_id not in self.nodes:
                self.add_node(sk_id, "skill", {"nombre": sk})
            self.add_edge(pid, sk_id, "DOMINA_SKILL", weight=1.2)

        # 2. Nodos de Logros y Conexiones a Skills
        for logro in logros:
            self.add_node(logro["id"], "logro", logro)
            self.add_edge(pid, logro["id"], "EJECUTO_LOGRO", weight=2.0)

            for sk in logro.get("skills") or []:
                sk_clean = str(sk).strip().lower()
                if not sk_clean:
                    continue
                sk_id = f"skill:{sk_clean}"
                if sk_id not in self.nodes:
                    self.add_node(sk_id, "skill", {"nombre": sk_clean})
                self.add_edge(logro["id"], sk_id, "DEMUESTRA_SKILL", weight=1.5)

        # 3. Leer todas las ofertas en ofertas/**/*.md
        offer_files = glob.glob(os.path.join(ROOT, "ofertas", "**", "*.md"), recursive=True)
        for fpath in offer_files:
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    parts = fh.read().split("---", 2)
                if len(parts) < 3:
                    continue
                o = yaml.safe_load(parts[1]) or {}
                if not o.get("id"):
                    continue

                oid = f"oferta:{o['id']}"
                self.add_node(oid, "oferta", {
                    "id_oferta": o["id"],
                    "empresa": o.get("empresa"),
                    "cargo": o.get("cargo"),
                    "estado": o.get("estado"),
                    "prioridad": o.get("prioridad"),
                    "match_score": o.get("match_score"),
                    "angulo": o.get("angulo"),
                    "salario": o.get("salario"),
                    "url": o.get("url")
                })

                empresa_id = f"empresa:{_slug(o.get('empresa') or 'empresa')}"
                if empresa_id not in self.nodes:
                    self.add_node(empresa_id, "empresa", {"nombre": o.get("empresa")})
                self.add_edge(oid, empresa_id, "PERTENECE_A")

                for kw in o.get("keywords", []) or []:
                    kw_clean = str(kw).lower().strip()
                    if not kw_clean:
                        continue
                    sk_id = f"skill:{kw_clean}"
                    if sk_id not in self.nodes:
                        self.add_node(sk_id, "skill", {"nombre": kw_clean})
                    self.add_edge(oid, sk_id, "REQUIERE_SKILL", weight=1.0)

                for br in o.get("brechas", []) or []:
                    br_clean = str(br).strip()
                    if not br_clean:
                        continue
                    br_id = f"brecha:{_slug(br_clean[:40])}"
                    if br_id not in self.nodes:
                        self.add_node(br_id, "brecha", {"descripcion": br_clean})
                    self.add_edge(oid, br_id, "TIENE_BRECHA", weight=1.0)

                if o.get("estado") == "postulado":
                    self.add_edge(pid, oid, "POSTULO_A", weight=3.0)

            except Exception:
                continue

    def match_oferta(self, id_o_slug):
        """Encuentra los logros con mayor cercanía relacional a una oferta."""
        target_node = None
        for nid, data in self.nodes.items():
            if data.get("type") == "oferta" and (id_o_slug in nid or id_o_slug in data.get("id_oferta", "")):
                target_node = data
                break

        if not target_node:
            return {"error": f"Oferta '{id_o_slug}' no encontrada en el grafo."}

        oid = target_node["id"]
        skills_oferta = {
            edge["to"] for edge in self.edges
            if edge["from"] == oid and edge["rel"] == "REQUIERE_SKILL"
        }

        scores_logros = []
        for logro in self.logros:
            lid = logro["id"]
            skills_logro = {
                edge["to"] for edge in self.edges
                if edge["from"] == lid and edge["rel"] == "DEMUESTRA_SKILL"
            }

            interseccion = []
            for s_logro in skills_logro:
                s_name = s_logro.replace("skill:", "")
                for s_of in skills_oferta:
                    of_name = s_of.replace("skill:", "")
                    if s_name in of_name or of_name in s_name:
                        interseccion.append(s_name)

            score = len(set(interseccion)) * 2.0
            if logro.get("angulo") == target_node.get("angulo"):
                score += 3.0

            scores_logros.append({
                "logro_id": lid,
                "titulo": logro["titulo"],
                "angulo": logro.get("angulo"),
                "ahorro": logro.get("ahorro"),
                "score_relacional": score,
                "skills_coincidentes": list(set(interseccion)),
                "descripcion": logro.get("descripcion", "")
            })

        scores_logros.sort(key=lambda x: x["score_relacional"], reverse=True)

        return {
            "oferta": {
                "id": target_node["id_oferta"],
                "empresa": target_node.get("empresa"),
                "cargo": target_node.get("cargo"),
                "angulo": target_node.get("angulo"),
                "skills_totales_detectadas": len(skills_oferta)
            },
            "top_logros_relacionales": scores_logros[:4]
        }

    def radar_brechas(self):
        conteo_brechas = {}
        for edge in self.edges:
            if edge["rel"] == "TIENE_BRECHA":
                bid = edge["to"]
                bdata = self.nodes.get(bid, {})
                desc = bdata.get("descripcion", bid)
                conteo_brechas[desc] = conteo_brechas.get(desc, 0) + 1

        ordenadas = sorted(conteo_brechas.items(), key=lambda x: x[1], reverse=True)
        return [{"brecha": k, "frecuencia": v} for k, v in ordenadas if v > 0]

    def exportar_mermaid(self, id_o_slug=None):
        nombre = self.perfil_node.get("nombre", "Usuario")
        lines = ["```mermaid", "flowchart TD"]
        lines.append(f'    Perfil["🧑 {nombre}"]:::perfil')

        if id_o_slug:
            match = self.match_oferta(id_o_slug)
            if "error" not in match:
                of = match["oferta"]
                lines.append(f'    Oferta["🏢 {of["empresa"]} — {of["cargo"]}"]:::oferta')
                for i, lg in enumerate(match["top_logros_relacionales"]):
                    lid = f"L{i}"
                    lines.append(f'    {lid}["🏆 {lg["titulo"]}\\n({lg["ahorro"]})"]:::logro')
                    lines.append(f'    Perfil --> {lid}')
                    lines.append(f'    {lid} -->|Score: {lg["score_relacional"]}| Oferta')
        else:
            for lg in self.logros[:5]:
                slug = lg["id"].replace("logro:", "")
                lines.append(f'    {slug}["🏆 {lg["titulo"]}"]:::logro')
                lines.append(f'    Perfil --> {slug}')

        lines.append("    classDef perfil fill:#1e293b,stroke:#3b82f6,stroke-width:2px,color:#fff;")
        lines.append("    classDef logro fill:#065f46,stroke:#10b981,stroke-width:2px,color:#fff;")
        lines.append("    classDef oferta fill:#7c2d12,stroke:#f97316,stroke-width:2px,color:#fff;")
        lines.append("```")
        return "\n".join(lines)

    def persistir(self):
        data = {
            "total_nodos": len(self.nodes),
            "total_aristas": len(self.edges),
            "nodos": self.nodes,
            "aristas": self.edges
        }
        os.makedirs(os.path.dirname(GRAFO_JSON_PATH), exist_ok=True)
        with open(GRAFO_JSON_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"✓ Grafo persistido en: {GRAFO_JSON_PATH} ({len(self.nodes)} nodos, {len(self.edges)} conexiones)")


def main():
    parser = argparse.ArgumentParser(description="Grafo de Conocimiento relacional de Carrera.")
    parser.add_argument("--construir", action="store_true", help="Construir o actualizar el grafo desde el repo.")
    parser.add_argument("--match", type=str, help="Encontrar los logros relacionales más fuertes para una oferta: --match <slug-o-id>")
    parser.add_argument("--radar-brechas", action="store_true", help="Mostrar radar de brechas acumuladas en el mercado.")
    parser.add_argument("--mermaid", type=str, nargs="?", const="default", help="Generar diagrama Mermaid de conexiones.")

    args = parser.parse_args()

    kg = KnowledgeGraph()
    kg.build_from_repo()

    if args.construir or (not args.match and not args.radar_brechas and not args.mermaid):
        kg.persistir()
        return

    if args.match:
        res = kg.match_oferta(args.match)
        if "error" in res:
            print(f"❌ {res['error']}")
            return
        of = res["oferta"]
        print(f"\n=======================================================")
        print(f"🎯 MATCH RELACIONAL DE GRAFO: {of['empresa']} — {of['cargo']}")
        print(f"Ángulo: {of['angulo']} | Skills detectadas: {of['skills_totales_detectadas']}")
        print(f"=======================================================\n")
        for i, lg in enumerate(res["top_logros_relacionales"], 1):
            print(f"{i}. {lg['titulo']} [Score: {lg['score_relacional']}]")
            print(f"   • Impacto: {lg['ahorro']}")
            print(f"   • Skills conectadas: {', '.join(lg['skills_coincidentes']) if lg['skills_coincidentes'] else 'Calce transversal'}")
            print(f"   • Detalle: {lg['descripcion']}\n")

    if args.radar_brechas:
        brechas = kg.radar_brechas()
        print(f"\n=======================================================")
        print(f"📊 RADAR DE BRECHAS RECURRENTES DEL MERCADO (GRAFO)")
        print(f"=======================================================\n")
        for b in brechas[:8]:
            print(f"• [Frecuencia: {b['frecuencia']} ofertas] {b['brecha']}")
        print()

    if args.mermaid:
        target = None if args.mermaid == "default" else args.mermaid
        print(kg.exportar_mermaid(target))


if __name__ == "__main__":
    main()
