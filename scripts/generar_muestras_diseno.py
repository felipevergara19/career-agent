#!/usr/bin/env python3
"""Genera los 4 diseños finales de 1 sola página (1/1) elegidos por Felipe:
  1. FinOps        -> Swiss Editorial Split Sidebar (`editorial`)
  2. AI Automation -> Bento KPI Dashboard + Timeline (`bento`)
  3. DevSecOps     -> Swiss Editorial Split Sidebar (`editorial`)
  4. SecOps / SIEM -> Linear Tech Banner + 2×2 Skill Grid (`tech-banner`)

Todos usando 100% hechos verificados de perfil/perfil-maestro.md, más una galería HTML 2×2.
"""

from __future__ import annotations

import html
from pathlib import Path
import shutil
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / ".agents" / "skills" / "adaptador-cv" / "scripts"))

import typst_cv_engine as tce  # noqa: E402

OUT_ROOT = ROOT / "cvs-generados" / "muestras-diseno"

COMMON_HEADER = {
    "name": "Felipe Vergara Peñaloza",
    "location": "Santiago, Chile (100% Remote · CLT/EST)",
    "email": "felipe.vergara19@gmail.com",
    "phone": "+56 9 5254 8117",
    "linkedin": "felipevergarap",
    "github": "felipevergara19",
}

COMMON_EDUCATION = [
    {
        "degree": "B.S. Computer & Info. Engineering",
        "institution": "Universidad Andrés Bello (UNAB)",
        "date": "May 2026",
    },
    {
        "degree": "B.A. English–Spanish Translation",
        "institution": "Instituto Profesional EATRI",
        "date": "2015 – 2019",
    },
    {
        "degree": "Programming Analyst Diploma",
        "institution": "INACAP",
        "date": "2010 – 2014",
    },
]

COMMON_CERTIFICATIONS = [
    {"name": "FinOps Certified Practitioner (FOCP)", "issuer": "FinOps Foundation / Linux Foundation", "date": "Nov 2026"},
    {"name": "Google Cybersecurity Certificate", "issuer": "Google", "date": ""},
    {"name": "Google Data Analytics Certificate", "issuer": "Google", "date": ""},
    {"name": "IBM AI Developer Certificate", "issuer": "IBM", "date": ""},
]

COMMON_LANGUAGES = [
    {"name": "English", "level": "C1 Certified", "pct": 92, "note": "Live technical & AWS billing vendor negotiations"},
    {"name": "Spanish", "level": "Native", "pct": 100, "note": "Native speaker (Chile)"},
]

TROLLBOX_COMPACT = {
    "company": "Trollbox Agency",
    "position": "Support & Security Escalation Specialist (Crypto Exchange)",
    "date": "2021 – 2023",
    "location": "100% Remote",
    "highlights": [
        "Bilingual (EN/ES) technical & security escalation for a crypto exchange: fraud triage, vulnerability escalation, and 2FA/MFA compliance.",
    ],
}

# Los 4 diseños finales elegidos por Felipe (condensados quirúrgicamente a 1 sola página 1/1)
SELECTED_DESIGNS: list[tuple[str, str]] = [
    ("finops", "editorial"),
    ("ai-automation", "bento"),
    ("devsecops", "editorial"),
    ("secops-siem", "tech-banner"),
]

THEMES_DATA: dict[str, dict[str, Any]] = {
    "finops": {
        "nombre_tema": "Cloud FinOps & AWS Cost Optimization",
        "descripcion_tema": "Diseño elegido: Swiss Editorial Split Sidebar (1 pág.) — Esmeralda + Grafito.",
        "headline": "FinOps Certified Practitioner (FOCP) & AWS Cloud Engineer · Cost Optimization & Multi-Account Governance",
        "kpis": [
            {"value": "~$24k+/yr", "label": "Direct AWS Savings", "sub": "Zombie ECS, VPC Endpoints, Logs"},
            {"value": "$2,500/mo", "label": "Savings Plans", "sub": "99.3% util · 4 AWS accounts"},
            {"value": "−50% / −40%", "label": "Right-Sizing", "sub": "ElastiCache & CloudWatch Logs"},
            {"value": "EN C1 + FOCP", "label": "Vendor Strategy", "sub": "3 AWS billing partners evaluated"},
        ],
        "summary": (
            "Bilingual (EN/ES C1) **FOCP-certified AWS Cloud Engineer** managing spend across a **4-account AWS organization "
            "(~$34.8k/mo main account)**. Delivered **~$24,000+/yr in direct cost cuts** and **$2,500/mo in Compute Savings Plans "
            "(99.3% util)** via zombie Fargate remediation, VPC Endpoints (~22 TB/mo NAT), **−40% logs**, and **−50% ElastiCache**."
        ),
        "skill_groups": [
            {
                "label": "Cloud FinOps",
                "tags": ["FOCP Certified", "Cost Explorer", "Savings Plans", "Right-Sizing", "VPC Endpoints", "Athena/S3 Cost", "Bedrock FinOps", "Billing Partners"],
            },
            {
                "label": "AWS & Governance",
                "tags": ["Organizations", "IAM Identity Center", "Least-Privilege IAM", "ECS Fargate", "ElastiCache", "CloudWatch", "CloudTrail", "EventBridge", "RDS", "KMS"],
            },
            {
                "label": "Automation & IaC",
                "tags": ["Python (Boto3)", "Terraform", "Ansible", "Docker", "SQL", "Bash", "n8n", "GPT-4o"],
            },
            {
                "label": "Security & SRE",
                "tags": ["GuardDuty", "incident.io", "Cloudflare WAF", "Wazuh SIEM", "SFTPGo/NLB"],
            },
        ],
        "experiences": [
            {
                "company": "Colektia",
                "position": "DevSecOps & Cloud FinOps Engineer",
                "date": "May 2026 – Present",
                "location": "100% Remote",
                "highlights": [
                    "**Captured ~$2,500/mo in AWS Compute Savings Plans (99.3% utilization):** led end-to-end renewal across 4 AWS accounts (~$34.8k/mo main account, up from ~$2,000/mo) and evaluated 3 AWS billing partners (*Pump, Blocks, Frust*) in English for leadership.",
                    "**Executed ~$24,000+/yr cost-reduction plan:** resolved a **+145.8% spend spike** (ECS +627%, NAT +107% at ~22 TB/mo), terminated **31 zombie ECS Fargate tasks**, and deployed 5 production **VPC Endpoints** (`ECR API/DKR`, `S3`, `Secrets Manager`, `Logs`).",
                    "**Cut CloudWatch Logs by ~40% & ElastiCache by −50%:** audited 2,154 GB/mo logs (`CrashLoopBackOff` fix + 30d retention), downsized Redis (`r6g.xlarge → r6g.large`, 0 evictions), and removed orphaned EBS volumes.",
                    "**Delivered S3/Athena & Bedrock cost diagnostics:** traced S3 `GET` spikes via CloudTrail to unpartitioned Athena queries (`BytesScannedCutoffPerQuery`) and evaluated Bedrock model sizing (Llama 3.3 70B vs. 8B).",
                    "**Governed 4 AWS accounts (~75 users) & SRE alerting:** authored least-privilege IAM policies (DynamoDB, Glue, SES, CodeBuild→EKS, MFA) via CLI/Boto3 and built **CloudWatch / GuardDuty → EventBridge → SNS → incident.io** pipelines.",
                ],
            },
            {
                "company": "Colektia",
                "position": "Cybersecurity, AI & Automation Engineer",
                "date": "2024 – Apr 2026",
                "location": "100% Remote",
                "highlights": [
                    "**Built AI Call QA (`n8n + Python + GPT-4o`):** evaluated **~10,000 daily calls** into BI dashboards and created an internal **Google Gemini RAG prototype**.",
                    "**Operated SecOps & VoIP stack:** managed Wazuh SIEM, Bitdefender EDR, Action1, Fleet, pfSense, Asterisk/Kamailio, and ran 2 Ethical Phishing campaigns (~100 users each).",
                ],
            },
            TROLLBOX_COMPACT,
        ],
        "project": {
            "name": "Career Agent System — Multi-Agent & FinOps for AI",
            "url": "https://github.com/felipevergara19/career-agent",
            "date": "Nov 2026 · Open Source",
            "highlights": [
                "Built **FinOps for AI** multi-LLM routing (OpenRouter, Gemini, Claude, Ollama), **0-token** collectors across 12 live ATS APIs, and **Neo4j GraphRAG** in Docker Compose (PostgreSQL 16, FastAPI).",
            ],
        },
    },
    "ai-automation": {
        "nombre_tema": "AI Automation, LLMs & Python",
        "descripcion_tema": "Diseño elegido: Bento KPI Dashboard + Timeline (1 pág.) — Índigo Espacial + Cian Eléctrico.",
        "headline": "AI Automation & AWS Cloud Engineer · Python, Production LLMs (~10k Calls/Day), GraphRAG & Event-Driven AWS",
        "kpis": [
            {"value": "~10,000/day", "label": "AI Call QA Scale", "sub": "n8n + Python + GPT-4o → BI"},
            {"value": "12 Live APIs", "label": "0-Token Ingestion", "sub": "Concurrent Python collectors"},
            {"value": "GraphRAG", "label": "Neo4j + Multi-LLM", "sub": "Cypher context · FinOps for AI"},
            {"value": "~$24k+/yr", "label": "AWS FinOps Impact", "sub": "FOCP + Bedrock diagnostics"},
        ],
        "summary": (
            "Bilingual (EN/ES C1) **AI Automation & AWS Cloud Engineer** building production LLM pipelines, Python/Boto3 "
            "automation, and event-driven AWS systems. Engineered an **AI Call QA pipeline (n8n, Python, GPT-4o)** evaluating "
            "**~10,000 daily calls**, an internal **Gemini RAG prototype**, and an open-source **Neo4j GraphRAG & FinOps for AI** platform."
        ),
        "skill_groups": [
            {
                "label": "AI & LLM Engineering",
                "tags": ["Python", "GPT-4o", "Claude", "Gemini RAG Prototype", "n8n", "Neo4j GraphRAG", "Cypher", "FinOps for AI", "Multi-LLM Routing"],
            },
            {
                "label": "Backend & AWS Events",
                "tags": ["FastAPI", "Boto3", "Docker Compose", "PostgreSQL 16", "REST APIs", "EventBridge", "SNS", "Lambda", "CloudWatch"],
            },
            {
                "label": "Cloud FinOps & Infra",
                "tags": ["FOCP Certified", "Bedrock FinOps", "Savings Plans", "VPC Endpoints", "ECS Fargate", "Terraform", "Ansible", "AWS IAM"],
            },
            {
                "label": "Real-Time & SecOps",
                "tags": ["incident.io", "GuardDuty", "Asterisk/Kamailio", "Twilio", "WhatsApp API", "Linux/Bash"],
            },
        ],
        "experiences": [
            {
                "company": "Colektia",
                "position": "DevSecOps & Cloud FinOps Engineer",
                "date": "May 2026 – Present",
                "location": "100% Remote",
                "highlights": [
                    "**Engineered event-driven alerting automation:** built **GuardDuty → EventBridge → SNS (KMS) → incident.io** and automated **SIP trunk monitoring** (`Terraform + Ansible + systemd → CloudWatch → SNS → incident.io` + `EventBridge Scheduler`), establishing an org-wide pattern.",
                    "**Executed ~$24,000+/yr AWS FinOps & Bedrock cost plan:** terminated **31 zombie ECS Fargate tasks**, deployed 5 **VPC Endpoints**, cut CloudWatch Logs **~40%**, right-sized ElastiCache **−50%**, and analyzed **Amazon Bedrock** (Llama 3.3 70B vs. 8B) savings.",
                    "**Captured ~$2,500/mo in Savings Plans (99.3% util)** across 4 AWS accounts and automated least-privilege IAM governance (~75 users) via AWS CLI & Python (Boto3).",
                ],
            },
            {
                "company": "Colektia",
                "position": "Cybersecurity, AI & Automation Engineer",
                "date": "2024 – Apr 2026",
                "location": "100% Remote",
                "highlights": [
                    "**Built production AI Call QA (`n8n + Python + GPT-4o`):** automated evaluation of **~10,000 daily collection calls** into structured BI dashboards, replacing manual review.",
                    "**Developed internal corporate RAG prototype:** built a functional **Google Gemini** assistant grounded strictly on internal docs to eliminate hallucinations.",
                    "**Operated VoIP & SecOps infrastructure:** managed Asterisk/PJSIP, Kamailio (Terraform/Ansible), Twilio, DIDWW, Voximplant, WhatsApp Business API, and Wazuh SIEM.",
                ],
            },
            TROLLBOX_COMPACT,
        ],
        "project": {
            "name": "Career Agent System — Multi-Agent, GraphRAG & FinOps for AI",
            "url": "https://github.com/felipevergara19/career-agent",
            "date": "Nov 2026 · Open Source",
            "highlights": [
                "Architected a **Python, FastAPI, Docker Compose, PostgreSQL 16 & Neo4j 5** multi-agent platform with **GraphRAG Cypher context injection** and per-role LLM routing (OpenRouter, Gemini, Claude, OpenAI, Ollama).",
                "Built concurrent **0-token** collectors across **12 live ATS/job APIs** (Greenhouse, Ashby, Lever, Get on Board, etc.) with atomic `fcntl` locks and a Linear-style Kanban UI.",
            ],
        },
    },
    "devsecops": {
        "nombre_tema": "DevOps / DevSecOps & AWS Cloud",
        "descripcion_tema": "Diseño elegido: Swiss Editorial Split Sidebar (1 pág.) — Navy Obsidiana + Ámbar AWS.",
        "headline": "DevSecOps & Cloud FinOps Engineer (FOCP) · Multi-Account AWS, Terraform/Ansible, SRE Alerting & Cost Optimization",
        "kpis": [
            {"value": "4 AWS Accts", "label": "IAM Governance", "sub": "~75 users · Organizations & SSO"},
            {"value": "TF + Ansible", "label": "SRE Alerting", "sub": "CloudWatch → SNS → incident.io"},
            {"value": "~$24k+/yr", "label": "Cloud Efficiency", "sub": "$2.5k/mo SP · 5 VPC Endpoints"},
            {"value": "EN C1", "label": "Global Remote", "sub": "Bilingual Cloud Operations"},
        ],
        "summary": (
            "Bilingual (EN/ES C1) **DevSecOps & Cloud FinOps Engineer (FOCP)** operating a **4-account AWS org (~75 users)** "
            "with impact across SRE alerting (**Terraform, Ansible, CloudWatch, GuardDuty, EventBridge, SNS, incident.io**), "
            "least-privilege IAM, edge hardening (**Cloudflare WAF, AWS SES, SFTPGo/NLB**), and **~$24k+/yr + $2.5k/mo** cloud savings."
        ),
        "skill_groups": [
            {
                "label": "AWS & IAM",
                "tags": ["Organizations", "IAM Identity Center", "Least-Privilege IAM", "ECS Fargate", "ElastiCache", "RDS MySQL", "DynamoDB", "VPC Endpoints", "NLB", "KMS"],
            },
            {
                "label": "IaC & SRE",
                "tags": ["Terraform", "Ansible", "Docker Compose", "Python (Boto3)", "Linux/systemd", "CloudWatch", "EventBridge", "SNS", "incident.io"],
            },
            {
                "label": "Cloud FinOps",
                "tags": ["FOCP Certified", "Cost Explorer", "Savings Plans", "Right-Sizing", "NAT Optimization", "EBS Hygiene"],
            },
            {
                "label": "Security & Edge",
                "tags": ["GuardDuty", "Cloudflare WAF", "AWS SES DKIM", "SFTPGo", "Wazuh SIEM", "Bitdefender EDR", "pfSense"],
            },
        ],
        "experiences": [
            {
                "company": "Colektia",
                "position": "DevSecOps & Cloud FinOps Engineer",
                "date": "May 2026 – Present",
                "location": "100% Remote",
                "highlights": [
                    "**Engineered automated SRE & security alerting (`Terraform + Ansible + AWS`):** built SIP trunk monitoring (`systemd → CloudWatch TrunkReachable → SNS → incident.io` + `EventBridge Scheduler`) and routed **GuardDuty → EventBridge → SNS (KMS) → incident.io**, adopted across teams.",
                    "**Governed 4 AWS accounts (~75 users) with least-privilege IAM:** managed Organizations, Identity Center, CLI & Boto3; authored scoped policies (DynamoDB blocking `Scan`/`DeleteItem`, Glue, SES `FromAddress`, CodeBuild→EKS, MFA), RDS MySQL roles, and SSL/PFX in Secrets Manager.",
                    "**Executed ~$24,000+/yr AWS cost-reduction plan:** resolved a +145.8% spike (ECS +627%, NAT +107% at ~22 TB/mo) by terminating **31 zombie ECS Fargate tasks** and deploying 5 production **VPC Endpoints**.",
                    "**Captured ~$2,500/mo via Compute Savings Plans (99.3% util)** and cut CloudWatch Logs by **~40%** (2,154 GB/mo audit + 30d retention) and ElastiCache by **−50%** (`r6g.xlarge → r6g.large`).",
                    "**Hardened edge, email & SFTPGo:** rotated **AWS SES DKIM (1024 → 2048-bit)** in Cloudflare (closing Bitsight finding), audited **Cloudflare WAF vs. AWS WAF**, and operated **SFTPGo behind AWS NLB** (Proxy Protocol).",
                ],
            },
            {
                "company": "Colektia",
                "position": "Cybersecurity, AI & Automation Engineer",
                "date": "2024 – Apr 2026",
                "location": "100% Remote",
                "highlights": [
                    "**Built AI & VoIP automation:** engineered an **n8n + Python + GPT-4o** QA pipeline (**~10,000 daily calls** into BI) and provisioned **Asterisk/PJSIP & Kamailio** servers with Terraform & Ansible.",
                    "**Administered corporate SecOps stack:** operated Wazuh SIEM, Bitdefender EDR, Action1, Fleet, and pfSense, running triangulated patch audits and 2 Ethical Phishing campaigns (~100 users each).",
                ],
            },
            TROLLBOX_COMPACT,
        ],
        "project": {
            "name": "Career Agent System — Containerized Multi-Agent Platform",
            "url": "https://github.com/felipevergara19/career-agent",
            "date": "Nov 2026 · Open Source",
            "highlights": [
                "Designed a non-root **Docker Compose** stack (**PostgreSQL 16, Neo4j 5, FastAPI**) with atomic `fcntl` locks, concurrent collectors across **12 live APIs**, and 0-token deterministic pipelines.",
            ],
        },
    },
    "secops-siem": {
        "nombre_tema": "Cloud Security, SecOps & SIEM",
        "descripcion_tema": "Diseño elegido: Linear Tech Banner + 2×2 Skill Grid (1 pág.) — Grafito + Carmesí (100% flujo lineal ATS).",
        "headline": "Cloud Security & SecOps Engineer · AWS IAM, GuardDuty, Wazuh SIEM, Automated IR & Edge Hardening",
        "kpis": [
            {"value": "GuardDuty → IO", "label": "Automated SecOps", "sub": "EventBridge + SNS (KMS) → incident.io"},
            {"value": "4 Accts / ~75", "label": "Least-Privilege IAM", "sub": "Organizations, SSO, MFA, KMS"},
            {"value": "2048-bit DKIM", "label": "Edge & Compliance", "sub": "Bitsight closed · WAF · Google CASA"},
            {"value": "Wazuh + EDR", "label": "Full SOC Stack", "sub": "Bitdefender, Action1, Fleet, pfSense"},
        ],
        "summary": (
            "Bilingual (EN/ES C1) **Cloud Security & SecOps Engineer** governing **4 AWS accounts (~75 users)** with event-driven "
            "alerting (**GuardDuty → EventBridge → SNS/KMS → incident.io**), email/edge hardening (**SES 2048-bit DKIM, Cloudflare WAF, "
            "SFTPGo/NLB, Google CASA & MS Graph OAuth**), and full SOC operations (**Wazuh SIEM, Bitdefender EDR, Action1, Fleet, pfSense**)."
        ),
        "skill_groups": [
            {
                "label": "AWS Cloud Security & IAM",
                "tags": ["AWS IAM", "Identity Center", "Organizations", "GuardDuty", "KMS", "Secrets Manager", "CloudTrail", "EventBridge", "AWS WAF", "SFTPGo/NLB"],
            },
            {
                "label": "SIEM, EDR & SecOps",
                "tags": ["Wazuh SIEM", "Bitdefender EDR/XDR", "Action1 MDM", "Fleet (osquery)", "pfSense/pfBlockerNG", "incident.io", "Ethical Phishing", "Patch Audits"],
            },
            {
                "label": "Email, Edge & OAuth",
                "tags": ["Cloudflare WAF", "Transform Rules", "SES 2048-bit DKIM", "Mailgun SPF/DKIM", "Bitsight", "Google CASA", "MS Graph OAuth"],
            },
            {
                "label": "FinOps & Automation",
                "tags": ["FOCP Certified", "Python (Boto3)", "Terraform", "Ansible", "Bash", "SQL", "VPC Endpoints", "Linux"],
            },
        ],
        "experiences": [
            {
                "company": "Colektia",
                "position": "DevSecOps & Cloud FinOps Engineer",
                "date": "May 2026 – Present",
                "location": "100% Remote",
                "highlights": [
                    "**Engineered automated security & incident response pipelines:** routed **Amazon GuardDuty** via **EventBridge & SNS (access policy + KMS)** into **incident.io** with source-tagged security routing, plus **CloudWatch → SNS → incident.io** alerting adopted org-wide.",
                    "**Governed 4 AWS accounts (~75 users) with least-privilege IAM:** managed Organizations, Identity Center, CLI & Boto3; authored scoped policies (DynamoDB blocking `Scan`/`DeleteItem` + PITR, Glue, SES `FromAddress`, CodeBuild→EKS, mandatory MFA), RDS MySQL roles, and SSL/PFX in Secrets Manager.",
                    "**Hardened email, edge (WAF), SFTPGo & OAuth compliance:** rotated **AWS SES Easy DKIM (1024 → 2048-bit)** in Cloudflare (closing **Bitsight** finding), audited **Cloudflare WAF vs. AWS WAF** (HTTP security Transform Rules), operated **SFTPGo behind AWS NLB** (Proxy Protocol + IP allowlists), and led **Google CASA (`gmail.send`)** & **Microsoft Graph (`Mail.Send`)** verification.",
                    "**Executed ~$24,000+/yr AWS FinOps hygiene:** terminated **31 zombie ECS Fargate tasks**, deployed 5 **VPC Endpoints**, cut CloudWatch Logs **~40%**, right-sized ElastiCache **−50%**, and captured **~$2,500/mo** in Savings Plans.",
                ],
            },
            {
                "company": "Colektia",
                "position": "Cybersecurity, AI & Automation Engineer",
                "date": "2024 – Apr 2026",
                "location": "100% Remote",
                "highlights": [
                    "**Operated corporate SOC stack & investigations:** managed **Wazuh SIEM, Bitdefender EDR/XDR, Action1, Fleet (osquery), and pfSense/pfBlockerNG**, investigating GuardDuty C&C/phishing alerts, running triangulated patch audits (`Action1 + Wazuh + Fleet`), and executing **2 Ethical Phishing campaigns (~100 users each)**.",
                    "**Built AI automation (`n8n + Python + GPT-4o`):** automated QA evaluation of **~10,000 daily calls** into BI dashboards and created an internal Gemini RAG prototype.",
                ],
            },
            TROLLBOX_COMPACT,
        ],
        "project": {
            "name": "Career Agent System (Hardened)",
            "url": "https://github.com/felipevergara19/career-agent",
            "date": "Nov 2026",
            "highlights": [
                "Non-root **Docker Compose** stack (PostgreSQL 16, Neo4j 5, FastAPI) with strict privacy/secret audits and atomic `fcntl` locks across 12 API collectors.",
            ],
        },
    },
}


def generate_gallery_html(results: list[dict[str, Any]]) -> Path:
    cards = []
    for item in results:
        imgs = "".join(
            f'<img src="{html.escape(p)}" alt="{html.escape(item["preset_id"])}" onclick="openModal(this.src, \'{html.escape(item["nombre_tema"])}\')" />'
            for p in item["png_rel_paths"]
        )
        pages = len(item["png_rel_paths"])
        badge_cls = "pages-ok" if pages == 1 else "pages-warn"
        cards.append(
            f"""
            <article class="card">
              <div class="card-header">
                <div class="badge-row">
                  <span class="swatch" style="background:{html.escape(item['color_dark'])}"></span>
                  <span class="swatch" style="background:{html.escape(item['color_accent'])}"></span>
                  <span class="preset-id">{html.escape(item['preset_id'])}</span>
                  <span class="{badge_cls}">{pages} pág.</span>
                </div>
                <h2>{html.escape(item['nombre_tema'])}</h2>
                <p class="desc">{html.escape(item['descripcion_tema'])}</p>
                <div class="actions">
                  <a class="btn-pdf" href="{html.escape(item['pdf_rel_path'])}" target="_blank">Abrir PDF (1 pág.)</a>
                  <span class="meta-chip">{html.escape(item['ats_nota'])}</span>
                </div>
              </div>
              <div class="preview-strip">
                {imgs}
              </div>
            </article>
            """
        )

    gallery = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tus 4 Diseños Finales (1 Página) — Felipe Vergara</title>
<style>
  :root {{
    --bg: #070a12;
    --surface: #111827;
    --surface-2: #1f2937;
    --border: #263042;
    --text: #f3f4f6;
    --muted: #9ca3af;
    --accent: #38bdf8;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.5;
  }}
  header {{
    position: sticky;
    top: 0;
    z-index: 20;
    background: rgba(7, 10, 18, 0.94);
    backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--border);
    padding: 16px 28px;
  }}
  header h1 {{ margin: 0; font-size: 1.2rem; }}
  header p {{ margin: 3px 0 0; color: var(--muted); font-size: 0.86rem; }}
  main {{ max-width: 1480px; margin: 0 auto; padding: 28px; }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(560px, 1fr));
    gap: 26px;
  }}
  .card {{
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 14px;
    overflow: hidden;
    display: flex;
    flex-direction: column;
  }}
  .card-header {{ padding: 16px 20px 14px; border-bottom: 1px solid var(--border); }}
  .badge-row {{ display: flex; align-items: center; gap: 7px; margin-bottom: 8px; }}
  .swatch {{ width: 14px; height: 14px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.25); }}
  .preset-id {{
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.78rem;
    color: #e5e7eb;
    background: var(--surface-2);
    padding: 2px 8px;
    border-radius: 6px;
  }}
  .pages-ok {{
    margin-left: auto;
    font-size: 0.75rem;
    font-weight: 700;
    color: #34d399;
    background: rgba(52, 211, 153, 0.14);
    padding: 2px 9px;
    border-radius: 999px;
  }}
  .pages-warn {{
    margin-left: auto;
    font-size: 0.75rem;
    font-weight: 700;
    color: #fbbf24;
    background: rgba(251, 191, 36, 0.14);
    padding: 2px 9px;
    border-radius: 999px;
  }}
  .card h2 {{ margin: 0 0 4px; font-size: 1.15rem; }}
  .desc {{ margin: 0 0 12px; color: var(--muted); font-size: 0.86rem; }}
  .actions {{ display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }}
  .btn-pdf {{
    background: #2563eb;
    color: #fff;
    text-decoration: none;
    font-size: 0.83rem;
    font-weight: 600;
    padding: 6px 13px;
    border-radius: 7px;
  }}
  .btn-pdf:hover {{ background: #1d4ed8; }}
  .meta-chip {{ font-size: 0.77rem; color: #93c5fd; }}
  .preview-strip {{
    padding: 16px;
    background: #0b1120;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }}
  .preview-strip img {{
    width: 100%;
    height: auto;
    border-radius: 6px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.55);
    cursor: zoom-in;
    background: #fff;
  }}
  .modal {{
    display: none;
    position: fixed;
    inset: 0;
    z-index: 50;
    background: rgba(0, 0, 0, 0.9);
    padding: 24px;
    overflow: auto;
    text-align: center;
  }}
  .modal.open {{ display: block; }}
  .modal img {{ max-width: 980px; width: 100%; height: auto; border-radius: 8px; background: #fff; }}
  .modal-close {{
    position: fixed;
    top: 16px;
    right: 24px;
    background: #ef4444;
    color: #fff;
    border: none;
    border-radius: 8px;
    padding: 8px 14px;
    font-weight: 700;
    cursor: pointer;
  }}
</style>
</head>
<body>
<header>
  <h1>Tus 4 Diseños Definitivos (1 Página Ejecutiva) — Felipe Vergara</h1>
  <p>FinOps (Editorial) · AI Automation (Bento) · DevSecOps (Editorial) · SecOps/SIEM (Tech Banner). Haz clic en cualquiera para ampliar.</p>
</header>
<main>
  <div class="grid">
    {''.join(cards)}
  </div>
</main>
<div id="modal" class="modal" onclick="closeModal()">
  <button class="modal-close" onclick="closeModal()">Cerrar (Esc)</button>
  <img id="modal-img" src="" alt="Vista ampliada" onclick="event.stopPropagation()" />
</div>
<script>
  function openModal(src, title) {{
    document.getElementById('modal-img').src = src;
    document.getElementById('modal-img').alt = title;
    document.getElementById('modal').classList.add('open');
  }}
  function closeModal() {{
    document.getElementById('modal').classList.remove('open');
  }}
  document.addEventListener('keydown', (e) => {{ if (e.key === 'Escape') closeModal(); }});
</script>
</body>
</html>
"""
    gallery_path = OUT_ROOT / "galeria.html"
    gallery_path.write_text(gallery, encoding="utf-8")
    return gallery_path


def main() -> int:
    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)
    OUT_ROOT.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []
    errors = 0

    for angulo, layout in SELECTED_DESIGNS:
        theme = THEMES_DATA[angulo]
        pal = tce.THEME_PALETTES[angulo]
        lmeta = tce.LAYOUT_META[layout]

        payload = {
            **COMMON_HEADER,
            "headline": theme["headline"],
            "kpis": theme["kpis"],
            "summary": theme["summary"],
            "skill_groups": theme["skill_groups"],
            "experiences": theme["experiences"],
            "project": theme["project"],
            "education": COMMON_EDUCATION,
            "certifications": COMMON_CERTIFICATIONS,
            "languages": COMMON_LANGUAGES,
        }

        angle_dir = OUT_ROOT / angulo
        angle_dir.mkdir(parents=True, exist_ok=True)

        preset_id = f"{angulo}--{layout}-1p"
        pdf_path = angle_dir / f"cv-felipe-vergara-{angulo}.pdf"
        png_prefix = angle_dir / preset_id

        ok, pngs, msg = tce.compile_cv(
            data=payload,
            angulo=angulo,
            layout=layout,
            pdf_path=pdf_path,
            png_prefix=png_prefix,
        )
        if not ok:
            print(f"[ERROR] {preset_id}: {msg}")
            errors += 1
            continue

        png_rels = [p.relative_to(OUT_ROOT).as_posix() for p in pngs]
        pdf_rel = pdf_path.relative_to(OUT_ROOT).as_posix()
        status_pg = "✓ 1 pág." if len(pngs) == 1 else f"⚠ {len(pngs)} pág."
        print(f"[OK] {preset_id:30s} -> {pdf_rel} ({status_pg})")
        results.append(
            {
                "preset_id": preset_id,
                "angulo": angulo,
                "nombre_tema": theme["nombre_tema"],
                "descripcion_tema": theme["descripcion_tema"],
                "ats_nota": lmeta["ats_nota"],
                "color_dark": pal["dark_bg"],
                "color_accent": pal["accent"],
                "pdf_rel_path": pdf_rel,
                "png_rel_paths": png_rels,
            }
        )

    gallery_path = generate_gallery_html(results)
    print(f"\n✓ Galería final 2×2 generada: {gallery_path.relative_to(ROOT)}")
    print(f"✓ Total generados: {len(results)}/4 (errores: {errors})")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
