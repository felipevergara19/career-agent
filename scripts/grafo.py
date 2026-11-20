#!/usr/bin/env python3
"""
Motor de Grafo de Conocimiento (Knowledge Graph) para Career Agent.
Conecta de forma relacional:
  (Felipe) ──► (Logros Impact Log) ──► (Skills/AWS) ◄── (Requisitos Ofertas)
                                                           │
                                                           ▼
                                                    (Radar Brechas)

Cero dependencias externas (utiliza estructuras nativas de Python y persistencia JSON).
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

# Definición de Nodos de Impacto y Logros verificados de Felipe (Fuente: perfil-maestro.md)
LOGROS_BASE = [
    {
        "id": "logro:cur_duckdb",
        "titulo": "FinOps Analytics $0-compute con DuckDB y AWS CUR",
        "angulo": "finops",
        "ahorro": "$0 compute / análisis directo en S3",
        "skills": ["aws cur", "duckdb", "s3", "python", "cost explorer", "finops", "sql", "partition pruning"],
        "metricas": "Auditoría masiva de CUR en S3 sin costo de Athena ni EMR usando httpfs.",
        "descripcion": "Diseño de workflow FinOps local a costo de cómputo $0 para auditar AWS CUR en S3 con DuckDB (httpfs)."
    },
    {
        "id": "logro:savings_plans",
        "titulo": "Ahorro $2,500/mo en AWS Savings Plans (99.3% uso)",
        "angulo": "finops",
        "ahorro": "$2,500 USD/mes",
        "skills": ["savings plans", "finops", "aws cost explorer", "commitments", "cost optimization", "focp"],
        "metricas": "Modelado de gasto histórico para estructurar AWS Compute Savings Plan con 99.3% de utilización sostenida.",
        "descripcion": "Estructuración y recomendación de Savings Plans en organización multi-cuenta de AWS."
    },
    {
        "id": "logro:elasticache_50",
        "titulo": "Reducción de 50% de costos en Amazon ElastiCache",
        "angulo": "finops",
        "ahorro": "~50% costo ElastiCache",
        "skills": ["elasticache", "redis", "right-sizing", "aws cloudwatch", "finops", "cost optimization"],
        "metricas": "Reducción del 50% en facturación de cluster Redis tras correlación de telemetría de CPU/RAM con cero degradación.",
        "descripcion": "Right-sizing de nodos ElastiCache basado en telemetría de consumo real."
    },
    {
        "id": "logro:eks_700",
        "titulo": "Ahorro ~$700/mes en Amazon EKS ($8,400/año)",
        "angulo": "finops",
        "ahorro": "$700 USD/mes (~$8,400 USD/año)",
        "skills": ["eks", "kubernetes", "cloudwatch container insights", "cloudtrail", "right-sizing", "autoscaling"],
        "metricas": "Ajuste de límites de pods y autoescalado en clústeres EKS, eliminando nodos ociosos.",
        "descripcion": "Optimización de costos y capacidad de cómputo en Amazon EKS."
    },
    {
        "id": "logro:vpc_endpoints",
        "titulo": "Recorte de tráfico NAT Gateway y logs CloudWatch (-40%)",
        "angulo": "finops",
        "ahorro": "~40% volumen de logs + reducción tráfico NAT",
        "skills": ["vpc endpoints", "nat gateway", "cloudwatch logs", "lambda", "networking", "finops"],
        "metricas": "VPC Endpoints para S3 y DynamoDB evitando cargos de NAT Gateway; políticas de retención optimizadas.",
        "descripcion": "Optimización de costos de red y retención de logs en AWS."
    },
    {
        "id": "logro:incident_response",
        "titulo": "Arquitectura Event-Driven de Incidentes (incident.io / GuardDuty)",
        "angulo": "secops-siem",
        "ahorro": "MTTR optimizado y triaje automático",
        "skills": ["guardduty", "cloudwatch", "eventbridge", "sns", "incident.io", "slack", "secops", "incident response"],
        "metricas": "Pipeline integral ruteando hallazgos de seguridad y alarmas hacia Slack e incident.io con triaje automático.",
        "descripcion": "Automatización event-driven de respuesta a incidentes en AWS."
    },
    {
        "id": "logro:iam_governance",
        "titulo": "Gobernanza IAM Least-Privilege y KMS multi-cuenta",
        "angulo": "secops-siem",
        "ahorro": "Superficie de ataque minimizada",
        "skills": ["iam", "kms", "sts", "least privilege", "aws cli", "boto3", "devsecops", "cloud security"],
        "metricas": "Auditoría de políticas por ARN/prefijo, controles de descifrado KMS y roles STS asumibles.",
        "descripcion": "Implementación de políticas de mínimo privilegio y control de acceso en AWS."
    },
    {
        "id": "logro:wazuh_siem",
        "titulo": "Hardening Wazuh 4.14.10 SIEM y erradicación devil-miner",
        "angulo": "secops-siem",
        "ahorro": "Disco de ~111 GB a ~30% / Erradicación malware",
        "skills": ["wazuh", "siem", "opensearch", "bitdefender", "edr", "incident remediation", "linux security"],
        "metricas": "Erradicación de criptominero persistente, políticas ISM en OpenSearch (30d hot/365d cold) y logrotate.",
        "descripcion": "Administración de SIEM Wazuh y respuesta a incidentes críticos de malware."
    },
    {
        "id": "logro:ai_eval_10k",
        "titulo": "Sistema de evaluación con IA en producción (10,000 llamadas/día)",
        "angulo": "ai-automation",
        "ahorro": "Control de calidad 100% automatizado",
        "skills": ["python", "llms", "ai automation", "rest apis", "bi dashboards", "boto3"],
        "metricas": "Procesamiento diario de 10k llamadas con modelos LLM y entrega de métricas hacia dashboards analíticos.",
        "descripcion": "Automatización de evaluación de llamadas y flujos de negocio con Python e IA."
    },
    {
        "id": "logro:ai_slack_rag",
        "titulo": "Asistente interno RAG en Slack con Gemini",
        "angulo": "ai-automation",
        "ahorro": "Acceso instantáneo a documentación interna",
        "skills": ["rag", "gemini", "embeddings", "vector search", "slack", "python", "ai automation"],
        "metricas": "Prototipo e implementación de asistente de búsqueda semántica y soporte a operaciones sobre Slack.",
        "descripcion": "Construcción de RAG corporativo para operaciones de soporte."
    },
    {
        "id": "logro:terraform_telephony",
        "titulo": "Infraestructura como Código (Terraform) y telefonía VoIP",
        "angulo": "devsecops",
        "ahorro": "Aprovisionamiento automatizado y reproducible",
        "skills": ["terraform", "iac", "voip", "docker", "github actions", "linux"],
        "metricas": "Módulos de Terraform para servidores SIP, networking y observabilidad automatizada.",
        "descripcion": "Aprovisionamiento de infraestructura cloud con Terraform."
    }
]


class KnowledgeGraph:
    def __init__(self):
        self.nodes = {}
        self.edges = []

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
        # 1. Nodo Perfil de Felipe
        self.add_node("perfil:felipe", "perfil", {
            "nombre": "Felipe Vergara Peñaloza",
            "titulo": "Cloud FinOps & DevSecOps Engineer",
            "certificaciones": ["FinOps Certified Practitioner (FOCP)", "Google Cybersecurity", "IBM AI Developer"],
            "idiomas": ["Inglés (C1)", "Español (Nativo)"]
        })

        # 2. Nodos de Logros y Conexiones a Skills
        for logro in LOGROS_BASE:
            self.add_node(logro["id"], "logro", logro)
            self.add_edge("perfil:felipe", logro["id"], "EJECUTO_LOGRO", weight=2.0)

            for sk in logro["skills"]:
                sk_id = f"skill:{sk}"
                if sk_id not in self.nodes:
                    self.add_node(sk_id, "skill", {"nombre": sk})
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

                # Empresa
                empresa_id = f"empresa:{o.get('empresa', '').lower().replace(' ', '-')}"
                if empresa_id not in self.nodes:
                    self.add_node(empresa_id, "empresa", {"nombre": o.get("empresa")})
                self.add_edge(oid, empresa_id, "PERTENECE_A")

                # Conectar Keywords de la oferta a Skills del grafo
                for kw in o.get("keywords", []) or []:
                    kw_clean = str(kw).lower().strip()
                    sk_id = f"skill:{kw_clean}"
                    if sk_id not in self.nodes:
                        self.add_node(sk_id, "skill", {"nombre": kw_clean})
                    self.add_edge(oid, sk_id, "REQUIERE_SKILL", weight=1.0)

                # Conectar Brechas
                for br in o.get("brechas", []) or []:
                    br_clean = str(br).strip()
                    br_id = f"brecha:{br_clean[:40].lower().replace(' ', '-')}"
                    if br_id not in self.nodes:
                        self.add_node(br_id, "brecha", {"descripcion": br_clean})
                    self.add_edge(oid, br_id, "TIENE_BRECHA", weight=1.0)

                # Estado de postulación
                if o.get("estado") == "postulado":
                    self.add_edge("perfil:felipe", oid, "POSTULO_A", weight=3.0)

            except Exception:
                continue

    def match_oferta(self, id_o_slug):
        """Encuentra los logros con mayor cercanía relacional a una oferta."""
        # Buscar el nodo de oferta
        target_node = None
        for nid, data in self.nodes.items():
            if data.get("type") == "oferta" and (id_o_slug in nid or id_o_slug in data.get("id_oferta", "")):
                target_node = data
                break

        if not target_node:
            return {"error": f"Oferta '{id_o_slug}' no encontrada en el grafo."}

        oid = target_node["id"]
        # Obtener skills requeridas por la oferta
        skills_oferta = set()
        for edge in self.edges:
            if edge["from"] == oid and edge["rel"] == "REQUIERE_SKILL":
                skills_oferta.add(edge["to"])

        # Puntuación relacional de cada logro
        scores_logros = []
        for logro in LOGROS_BASE:
            lid = logro["id"]
            # Skills demostradas por este logro
            skills_logro = set()
            for edge in self.edges:
                if edge["from"] == lid and edge["rel"] == "DEMUESTRA_SKILL":
                    skills_logro.add(edge["to"])

            # Coincidencias directas y parciales
            interseccion = []
            for s_logro in skills_logro:
                s_name = s_logro.replace("skill:", "")
                for s_of in skills_oferta:
                    of_name = s_of.replace("skill:", "")
                    if s_name in of_name or of_name in s_name:
                        interseccion.append(s_name)

            score = len(interseccion) * 2.0
            if logro["angulo"] == target_node.get("angulo"):
                score += 3.0

            scores_logros.append({
                "logro_id": lid,
                "titulo": logro["titulo"],
                "angulo": logro["angulo"],
                "ahorro": logro["ahorro"],
                "score_relacional": score,
                "skills_coincidentes": list(set(interseccion)),
                "descripcion": logro["descripcion"]
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
        """Calcula qué brechas son las más recurrentes en el grafo y bloquean postulaciones."""
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
        """Genera diagrama Mermaid visualizable en Markdown."""
        lines = ["```mermaid", "flowchart TD"]
        lines.append('    Felipe["🧑 Felipe Vergara (FOCP)"]:::perfil')

        if id_o_slug:
            match = self.match_oferta(id_o_slug)
            if "error" not in match:
                of = match["oferta"]
                lines.append(f'    Oferta["🏢 {of["empresa"]} — {of["cargo"]}"]:::oferta')
                for i, lg in enumerate(match["top_logros_relacionales"]):
                    lid = f"L{i}"
                    lines.append(f'    {lid}["🏆 {lg["titulo"]}\\n({lg["ahorro"]})"]:::logro')
                    lines.append(f'    Felipe --> {lid}')
                    lines.append(f'    {lid} -->|Score: {lg["score_relacional"]}| Oferta')
        else:
            # Resumen de los 4 ángulos de impacto
            for lg in LOGROS_BASE[:5]:
                slug = lg["id"].replace("logro:", "")
                lines.append(f'    {slug}["🏆 {lg["titulo"]}"]:::logro')
                lines.append(f'    Felipe --> {slug}')

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
        print("🏆 TOP LOGROS DE FELIPE RECOMENDADOS POR EL GRAFO:\n")
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
