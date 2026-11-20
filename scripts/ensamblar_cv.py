#!/usr/bin/env python3
"""
Ensamblador modular y determinista de CVs (0 tokens de LLM para ángulos estándar).
Toma los logros del Perfil Maestro (Impact Log) y ensambla un CV optimizado para ATS
reordenando el titular, resumen ejecutivo, skills y bullets de Colektia según el ángulo del rol.
Genera automáticamente el Markdown, HTML y PDF llamando a render_cv.py.
"""

import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import yaml

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
RENDER_SCRIPT = os.path.join(ROOT, ".agents", "skills", "adaptador-cv", "scripts", "render_cv.py")

def _cargar_datos_personales() -> dict[str, str]:
    """Lee datos de contacto desde perfil/perfil-maestro.md si existe (sin hardcodear PII en el repo)."""
    datos = {
        "nombre": "FELIPE VERGARA PEÑALOZA",
        "ubicacion_en": "Santiago, Chile (100% Remote / Global Mobility — CLT / EST / CST) | Bilingual English (C1) & Spanish (Native)",
        "ubicacion_es": "Santiago, Chile (100% Remoto / Disponibilidad Global — CLT / EST / CST) | Bilingüe Inglés (C1) y Español (Nativo)",
        "contacto": "[tu-email@ejemplo.com](mailto:tu-email@ejemplo.com) | +XX X XXXX XXXX | [linkedin.com/in/tu-perfil](https://www.linkedin.com/in/tu-perfil) | [github.com/tu-usuario](https://github.com/tu-usuario)",
    }
    perfil_path = os.path.join(ROOT, "perfil", "perfil-maestro.md")
    if not os.path.exists(perfil_path):
        return datos

    try:
        with open(perfil_path, encoding="utf-8") as f:
            texto = f.read()
    except OSError:
        return datos

    def _extraer(campo: str) -> str | None:
        m = re.search(rf"^\s*-\s*\*\*{campo}:\*\*\s*`?([^`\n]+)`?", texto, re.MULTILINE)
        return m.group(1).strip() if m else None

    nombre = _extraer("Nombre completo")
    if nombre:
        datos["nombre"] = nombre.upper()

    partes = []
    email = _extraer("Email")
    if email:
        partes.append(f"[{email}](mailto:{email})")
    telefono = _extraer("Teléfono")
    if telefono:
        partes.append(telefono)
    linkedin = _extraer("LinkedIn")
    if linkedin:
        label = re.sub(r"^https?://(www\.)?", "", linkedin).rstrip("/")
        partes.append(f"[{label}]({linkedin})")
    github = _extraer("GitHub")
    if github:
        label = re.sub(r"^https?://(www\.)?", "", github).rstrip("/")
        partes.append(f"[{label}]({github})")
    if partes:
        datos["contacto"] = " | ".join(partes)
    return datos


DATOS_PERSONALES = _cargar_datos_personales()

TITULARES = {
    "finops": {
        "en": "FinOps Certified Practitioner (FOCP) & AWS Cloud Engineer | Cloud Cost Optimization (~$24k+/yr), Multi-Account AWS Governance & Python/AI Automation",
        "es": "FinOps Certified Practitioner (FOCP) & Ingeniero Cloud AWS | Optimización de Costos (~$24k+/año), Gobierno AWS Multi-Cuenta y Automatización Python/IA"
    },
    "secops-siem": {
        "en": "Cloud Security & SecOps Engineer | AWS IAM, GuardDuty, Wazuh SIEM, Automated Incident Response (incident.io) & Email/Edge Security",
        "es": "Ingeniero de Seguridad Cloud & SecOps | AWS IAM, GuardDuty, Wazuh SIEM, Respuesta Automatizada a Incidentes (incident.io) y Seguridad de Borde/Correo"
    },
    "ai-automation": {
        "en": "AI Automation & AWS Cloud Engineer | Python, LLMs (~10,000 Calls/Day QA), n8n, REST APIs & Event-Driven AWS Workflows",
        "es": "Ingeniero de Automatización con IA & Cloud AWS | Python, LLMs (~10,000 Llamadas/Día QA), n8n, APIs y Flujos Event-Driven en AWS"
    },
    "devsecops": {
        "en": "DevSecOps & Cloud FinOps Engineer (FOCP Certified) | AWS Multi-Account IAM, Cost Optimization (~$24k+/yr), GuardDuty & Incident Automation",
        "es": "Ingeniero DevSecOps & Cloud FinOps (FOCP Certificado) | AWS Multi-Cuenta IAM, Optimización de Costos (~$24k+/año), GuardDuty y Automatización"
    },
    "base": {
        "en": "Cloud FinOps & DevSecOps Engineer (FOCP Certified) | AWS Cost Optimization, IAM, Security & AI Automation (n8n, Python, LLMs) | Bilingual EN/ES (C1)",
        "es": "Ingeniero Cloud FinOps & DevSecOps (FOCP Certificado) | Optimización de Costos AWS, IAM, Seguridad y Automatización con IA (n8n, Python, LLMs) | Bilingüe EN/ES (C1)"
    }
}

SUMMARIES = {
    "finops": {
        "en": "Bilingual (EN/ES C1) **Cloud FinOps & AWS Cloud Engineer** and **FinOps Certified Practitioner (FOCP)** managing cloud spend across a **4-account AWS organization (~$34.8k/mo main account)**. Proven track record executing **~$24,000+/year in direct cost reductions** and capturing **$2,500 USD/month via Compute Savings Plans (99.3% utilization)** through zombie ECS Fargate remediation, production VPC Endpoints (~22 TB/mo NAT traffic), **~40% CloudWatch Logs reduction**, and **−50% Amazon ElastiCache right-sizing**. Experienced negotiating in English with AWS billing partners and translating telemetry into engineering action.",
        "es": "Ingeniero Bilingüe (EN/ES C1) **Cloud FinOps & AWS** y **FinOps Certified Practitioner (FOCP)** administrando el gasto de una **organización AWS de 4 cuentas (~$34.8k/mes cuenta principal)**. Historial comprobado ejecutando **~$24,000+/año en reducción directa de costos** y capturando **$2,500 USD/mes mediante Compute Savings Plans (99.3% utilización)** con caza de tasks ECS Fargate zombie, VPC Endpoints (~22 TB/mes en NAT Gateway), **~40% menos gasto en CloudWatch Logs** y **−50% en Amazon ElastiCache**."
    },
    "secops-siem": {
        "en": "Bilingual (EN/ES C1) **Cloud Security & SecOps Engineer** with 2+ years of hands-on experience in multi-account AWS IAM governance (**4 accounts, ~75 users**), event-driven incident response, and enterprise SIEM/EDR operations. Engineered scalable alerting pipelines (**GuardDuty → EventBridge → SNS with KMS → incident.io**), hardened email/edge security (**AWS SES 2048-bit DKIM, Cloudflare WAF & Transform Rules, SFTPGo behind AWS NLB**), and operated **Wazuh SIEM, Bitdefender EDR, Action1, Fleet (osquery), and pfSense**.",
        "es": "Ingeniero Bilingüe (EN/ES C1) de **Seguridad Cloud & SecOps** con 2+ años de experiencia práctica en gobierno AWS IAM multi-cuenta (**4 cuentas, ~75 usuarios**), respuesta automatizada a incidentes y operación SIEM/EDR. Diseñé pipelines escalables (**GuardDuty → EventBridge → SNS con KMS → incident.io**), endurecí seguridad de correo y borde (**AWS SES DKIM 2048-bit, Cloudflare WAF, SFTPGo detrás de AWS NLB**) y operé **Wazuh SIEM, Bitdefender EDR, Action1, Fleet y pfSense**."
    },
    "ai-automation": {
        "en": "Bilingual (EN/ES C1) **AI Automation & AWS Cloud Engineer** specializing in production LLM workflows, Python/Boto3 scripting, and event-driven AWS automation. Built an automated **AI Call QA system (n8n, Python, GPT-4o)** evaluating **~10,000 daily calls** into BI dashboards, developed an internal **Gemini RAG prototype**, and automated cloud observability and FinOps operations across a 4-account AWS organization.",
        "es": "Ingeniero Bilingüe (EN/ES C1) de **Automatización con IA & Cloud AWS** especializado en flujos LLM en producción, scripting Python/Boto3 y automatización event-driven en AWS. Construí un **sistema automatizado de QA de llamadas con IA (n8n, Python, GPT-4o)** evaluando **~10,000 llamadas diarias** hacia dashboards BI, desarrollé un prototipo RAG interno con Gemini y automaticé observabilidad y FinOps en AWS."
    },
    "devsecops": {
        "en": "Bilingual (EN/ES C1) **DevSecOps & Cloud FinOps Engineer (FOCP Certified)** managing a **4-account AWS organization (~75 users)** with focus on cost optimization (**~$24,000+/year + $2,500/mo Savings Plans**), least-privilege IAM, event-driven incident automation (**CloudWatch, GuardDuty, EventBridge, SNS, incident.io**), and edge/email security (**Cloudflare WAF, AWS SES, SFTPGo/NLB**).",
        "es": "Ingeniero Bilingüe (EN/ES C1) **DevSecOps & Cloud FinOps (FOCP Certificado)** gestionando una **organización AWS de 4 cuentas (~75 usuarios)** con foco en optimización de costos (**~$24,000+/año + $2,500/mes en Savings Plans**), IAM de mínimo privilegio, respuesta automatizada a incidentes (**CloudWatch, GuardDuty, EventBridge, SNS, incident.io**) y seguridad de borde/correo."
    },
    "base": {
        "en": "Bilingual (EN/ES C1) **Cloud FinOps & DevSecOps Engineer (FOCP Certified)** managing a **4-account AWS organization (~$34.8k/mo)** with proven impact in cloud cost optimization (**~$24,000+/year + $2,500/mo Savings Plans**), multi-account IAM governance (~75 users), event-driven incident response (**GuardDuty / CloudWatch → EventBridge → SNS → incident.io**), SecOps (**Wazuh, Bitdefender, Cloudflare WAF**), and production AI automation (**~10,000 calls/day**).",
        "es": "Ingeniero Bilingüe (EN/ES C1) **Cloud FinOps & DevSecOps (FOCP Certificado)** gestionando una **organización AWS de 4 cuentas (~$34.8k/mes)** con impacto comprobado en optimización de costos (**~$24,000+/año + $2,500/mes en Savings Plans**), gobierno IAM multi-cuenta (~75 usuarios), respuesta automatizada a incidentes (**GuardDuty / CloudWatch → EventBridge → SNS → incident.io**), SecOps y automatización con IA (**~10,000 llamadas/día**)."
    }
}

BULLETS_COLEKTIA = {
    "savings_plans": {
        "en": "- **Captured ~$2,500 USD/month in ongoing AWS savings (99.3% utilization)** by leading end-to-end Compute Savings Plans renewal across a 4-account AWS organization (~$34.8k/mo main account), and evaluated 3 AWS billing partners (*Pump, Blocks, Frust*) in English for leadership.",
        "es": "- **Capturé ~$2,500 USD/mes de ahorro continuo en AWS (99.3% de utilización)** liderando de punta a punta la renovación de *Compute Savings Plans* en una organización de 4 cuentas (~$34.8k/mes cuenta principal), además de evaluar en inglés a 3 partners de facturación AWS (*Pump, Blocks, Frust*)."
    },
    "fargate_vpc_endpoints": {
        "en": "- **Executed a ~$24,000+ USD/year AWS cost-reduction plan:** investigated a **+145.8% spend spike** (ECS +627%, NAT Gateway +107% at ~22 TB/mo), terminated **31 zombie ECS Fargate tasks** stuck in infinite loops via Cost Explorer/CloudWatch/CloudTrail, and deployed production **VPC Endpoints** (`ECR API`, `ECR DKR`, `S3 Gateway`, `Secrets Manager`, `CloudWatch Logs`).",
        "es": "- **Ejecuté un plan de reducción de costos AWS de ~$24,000+ USD/año:** investigué una escalada de **+145.8%** (ECS +627%, NAT Gateway +107% con ~22 TB/mes), eliminé **31 tasks ECS Fargate zombie** en loop infinito y desplegué **VPC Endpoints** en producción (`ECR API`, `ECR DKR`, `S3 Gateway`, `Secrets Manager`, `CloudWatch Logs`)."
    },
    "logs_elasticache_hygiene": {
        "en": "- **Cut CloudWatch Logs / FluentBit / Lambda costs by ~40%** (from 2,154 GB/mo via `CrashLoopBackOff` cleanup, noise filtering, and 30-day retention), **right-sized Amazon ElastiCache by −50%** (`r6g.xlarge → r6g.large`, zero evictions), removed orphaned **EBS volumes**, and diagnosed S3/Athena Data Lake and Amazon Bedrock cost drivers.",
        "es": "- **Reduje ~40% el gasto en CloudWatch Logs / FluentBit / Lambda** (desde 2,154 GB/mes eliminando pods en *CrashLoopBackOff*, filtrando ruido y aplicando retención de 30 días), **reduje Amazon ElastiCache en −50%** (`r6g.xlarge → r6g.large`), eliminé volúmenes **EBS huérfanos** y diagnostiqué drivers de costo en S3/Athena y Amazon Bedrock."
    },
    "iam_governance": {
        "en": "- **Governed 4 AWS accounts (~75 users)** via AWS Organizations, IAM Identity Center, AWS CLI, and Python (Boto3), authoring least-privilege IAM policies (DynamoDB, AWS Glue, SES, CodeBuild→EKS, mandatory MFA), RDS MySQL role separation, and SSL/PFX certificate management in Secrets Manager.",
        "es": "- **Goberné 4 cuentas AWS (~75 usuarios)** con AWS Organizations, IAM Identity Center, AWS CLI y Python (Boto3), diseñando políticas IAM de mínimo privilegio (DynamoDB, AWS Glue, SES, CodeBuild→EKS, MFA obligatorio), permisos diferenciados en RDS MySQL y certificados SSL/PFX en Secrets Manager."
    },
    "incident_response": {
        "en": "- **Engineered event-driven security & SRE alerting pipelines:** routed **Amazon GuardDuty → EventBridge → SNS (KMS) → incident.io** and built automated **SIP trunk monitoring** (`Terraform + Ansible + CloudWatch custom metric → SNS → incident.io` with `EventBridge Scheduler` maintenance windows), establishing an organization-wide alerting pattern.",
        "es": "- **Diseñé pipelines event-driven de seguridad y SRE:** enruté **Amazon GuardDuty → EventBridge → SNS (KMS) → incident.io** y construí monitoreo automatizado de troncales SIP (`Terraform + Ansible + métrica CloudWatch → SNS → incident.io` con ventanas en `EventBridge Scheduler`), patrón adoptado por otros equipos."
    },
    "edge_email_oauth": {
        "en": "- **Hardened email, edge (WAF), SFTPGo, and OAuth compliance:** rotated **AWS SES Easy DKIM (1024 → 2048-bit)** in Cloudflare (closing a Bitsight finding), configured Mailgun SPF/DKIM, audited **Cloudflare WAF vs. AWS WAF** (applying Transform Rules), operated **SFTPGo behind AWS NLB** (Proxy Protocol), and led **Google CASA (`gmail.send`)** and **Microsoft Graph (`Mail.Send`)** OAuth verification.",
        "es": "- **Endurecí correo, borde (WAF), SFTPGo y cumplimiento OAuth:** roté llaves **AWS SES Easy DKIM (1024 → 2048 bits)** en Cloudflare (cerrando hallazgo Bitsight), configuré SPF/DKIM en Mailgun, audité **Cloudflare WAF vs. AWS WAF**, operé **SFTPGo detrás de AWS NLB** (Proxy Protocol) y lideré verificación OAuth de **Google (CASA)** y **Microsoft Graph**."
    },
    "secops_siem": {
        "en": "- **Operated corporate SecOps stack & investigations:** managed **Wazuh SIEM**, **Bitdefender (EDR/XDR)**, **Action1 (MDM/RMM)**, **Fleet (osquery)**, and **pfSense/pfBlockerNG**; investigated GuardDuty C&C/phishing alerts, defined triangulated patch audits (`Action1 + Wazuh + Fleet`), and ran **2 Ethical Phishing campaigns (~100 users each)**.",
        "es": "- **Operé el stack corporativo de SecOps e investigaciones:** administré **Wazuh SIEM**, **Bitdefender (EDR/XDR)**, **Action1 (MDM/RMM)**, **Fleet (osquery)** y **pfSense/pfBlockerNG**; investigué alertas GuardDuty (C&C/phishing DNS), definí auditorías trianguladas de parches y ejecuté **2 campañas de Ethical Phishing (~100 personas cada una)**."
    },
    "ai_automation": {
        "en": "- **Built a production AI Call QA system (`n8n + Python + GPT-4o`)** evaluating **~10,000 daily collection calls** into structured BI dashboards (replacing manual review), and developed an internal **Google Gemini RAG assistant prototype** grounded on corporate documentation.",
        "es": "- **Construí un sistema de QA de llamadas con IA en producción (`n8n + Python + GPT-4o`)** evaluando **~10,000 llamadas diarias** hacia dashboards BI (reemplazando revisión manual) y un **prototipo de asistente interno RAG con Google Gemini** acotado a documentación corporativa."
    },
    "voip_iac": {
        "en": "- **Operated cloud telephony/VoIP infrastructure:** managed **Asterisk/PJSIP and Kamailio** (provisioned with **Terraform & Ansible**), Twilio, DIDWW, Voximplant, and WhatsApp Business API.",
        "es": "- **Operé infraestructura de telefonía/VoIP:** gestioné **Asterisk/PJSIP y Kamailio** (aprovisionados con **Terraform y Ansible**), Twilio, DIDWW, Voximplant y WhatsApp Business API."
    }
}

ORDEN_BULLETS = {
    "finops": ["savings_plans", "fargate_vpc_endpoints", "logs_elasticache_hygiene", "iam_governance", "incident_response", "ai_automation"],
    "secops-siem": ["incident_response", "iam_governance", "edge_email_oauth", "secops_siem", "fargate_vpc_endpoints", "ai_automation"],
    "ai-automation": ["ai_automation", "incident_response", "fargate_vpc_endpoints", "savings_plans", "iam_governance", "voip_iac"],
    "devsecops": ["fargate_vpc_endpoints", "savings_plans", "iam_governance", "incident_response", "edge_email_oauth", "logs_elasticache_hygiene", "secops_siem"],
    "base": ["savings_plans", "fargate_vpc_endpoints", "logs_elasticache_hygiene", "iam_governance", "incident_response", "edge_email_oauth", "ai_automation"]
}


def ensamblar_cv(angulo="finops", idioma="en", custom_headline=None, custom_summary=None, custom_bullets=None):
    angulo = angulo.lower() if angulo in TITULARES else "finops"
    idioma = idioma.lower() if idioma in ["en", "es"] else "en"

    titular = custom_headline or TITULARES[angulo][idioma]
    summary = custom_summary or SUMMARIES[angulo][idioma]
    
    bullets_keys = custom_bullets or ORDEN_BULLETS[angulo]
    bullets_md = "\n".join(BULLETS_COLEKTIA[k][idioma] for k in bullets_keys if k in BULLETS_COLEKTIA)

    # Core Skills reordenados según ángulo (100% alineados a perfil/perfil-maestro.md)
    if angulo == "finops":
        skills_p1 = "**Cloud FinOps & Cost Management:** FinOps Certified Practitioner (FOCP), AWS Cost Explorer, Compute Savings Plans, Multi-Account Cost Governance, ECS Fargate / ElastiCache Right-Sizing, VPC Endpoints (NAT Traffic Reduction), CloudWatch Logs Optimization, S3/Athena & Bedrock Cost Diagnostics, Billing Partner Evaluation."
        skills_p2 = "**AWS Cloud & IAM Governance:** AWS Organizations, IAM Identity Center, AWS IAM (Least Privilege, MFA, Cross-Account), KMS, Secrets Manager, CloudWatch, CloudTrail, EventBridge, SNS, Lambda, ECS/Fargate, ElastiCache, RDS (MySQL), DynamoDB, AWS Glue, S3, SES, NLB."
        skills_p3 = "**Security, Observability & Edge:** Amazon GuardDuty, incident.io, Cloudflare WAF & Transform Rules, AWS WAF, Wazuh SIEM, Bitdefender EDR, Action1, Fleet (osquery), pfSense, SFTPGo, DKIM/SPF, OAuth Compliance (Google CASA / MS Graph)."
        skills_p4 = "**Automation, IaC & AI Engineering:** Python (Boto3, Automation), Bash, SQL, Terraform, Ansible, n8n, LLMs (GPT-4o, Gemini RAG Prototype), REST APIs, Linux."
    elif angulo == "secops-siem":
        skills_p1 = "**Cloud Security, SecOps & Edge:** Amazon GuardDuty, AWS IAM & IAM Identity Center (Least Privilege, MFA), AWS Organizations, KMS, Secrets Manager, Cloudflare WAF & Transform Rules, AWS WAF, AWS SES (Easy DKIM 2048-bit), Mailgun (SPF/DKIM), SFTPGo (AWS NLB + Proxy Protocol), OAuth Compliance (Google CASA / Microsoft Graph)."
        skills_p2 = "**SIEM, EDR & Incident Automation:** Wazuh SIEM, Bitdefender (EDR/XDR), Action1 (MDM/RMM), Fleet (osquery), pfSense / pfBlockerNG, incident.io, EventBridge, SNS, CloudWatch, CloudTrail, Ethical Phishing & Patch Auditing."
        skills_p3 = "**Cloud FinOps & AWS Infrastructure:** FinOps Certified Practitioner (FOCP), AWS Cost Explorer, Compute Savings Plans, VPC Endpoints, ECS/Fargate, ElastiCache, RDS, Lambda, S3."
        skills_p4 = "**Automation & AI Engineering:** Python (Boto3), Bash, SQL, Terraform, Ansible, n8n, LLMs (GPT-4o, Gemini RAG Prototype), Linux."
    elif angulo == "ai-automation":
        skills_p1 = "**AI Automation & Workflows:** Python (Boto3, Automation), LLMs (GPT-4o, Google Gemini RAG Prototype), n8n, REST APIs, Webhooks, EventBridge, SNS, CloudWatch, BI Data Pipelines."
        skills_p2 = "**AWS Cloud & FinOps:** FinOps Certified Practitioner (FOCP), AWS Organizations, IAM, Lambda, ECS/Fargate, VPC Endpoints, Cost Explorer, Compute Savings Plans, Amazon Bedrock Cost Analysis, S3, DynamoDB, RDS."
        skills_p3 = "**Observability, Security & IaC:** incident.io, Amazon GuardDuty, Terraform, Ansible, Cloudflare WAF, Wazuh SIEM, Linux, Bash, SQL."
        skills_p4 = "**Telephony / VoIP:** Asterisk/PJSIP, Kamailio, Twilio, DIDWW, Voximplant, WhatsApp Business API."
    else:
        skills_p1 = "**AWS Cloud & DevSecOps:** AWS Organizations, IAM Identity Center, AWS IAM (Least Privilege, MFA), Amazon GuardDuty, KMS, Secrets Manager, CloudWatch, CloudTrail, EventBridge, SNS, VPC Endpoints, ECS/Fargate, ElastiCache, RDS, S3, NLB."
        skills_p2 = "**Cloud FinOps & Cost Management:** FinOps Certified Practitioner (FOCP), AWS Cost Explorer, Compute Savings Plans, Right-Sizing (ElastiCache, Logs, Fargate), NAT Traffic Optimization, EBS Hygiene."
        skills_p3 = "**SecOps, Edge & Incident Response:** incident.io, Cloudflare WAF, AWS WAF, AWS SES (DKIM 2048-bit), SFTPGo, Wazuh SIEM, Bitdefender EDR, Action1, Fleet (osquery), pfSense."
        skills_p4 = "**Automation, IaC & AI Engineering:** Python (Boto3), Bash, SQL, Terraform, Ansible, n8n, LLMs (GPT-4o, Gemini RAG Prototype), Linux."

    hoy = datetime.date.today().strftime("%Y-%m-%d")

    if idioma == "en":
        exp_header = "## PROFESSIONAL EXPERIENCE"
        role_header = "### **Colektia** — *DevSecOps & Cloud FinOps Engineer (May 2026 – Present) | Previously: Cybersecurity, AI & Automation Engineer (2024 – Apr 2026)*"
        role_sub = "*Santiago, Chile (100% Remote) | 2024 – Present*"
        trollbox_title = "### **Trollbox Agency** — *Support & Security Escalation Specialist (Crypto Exchange)*"
        trollbox_sub = "*100% Remote | 2021 – 2023*"
        trollbox_bullet = "- Delivered bilingual (EN/ES) technical operations and security support for a cryptocurrency exchange, escalating fraud vectors, triaging platform vulnerabilities, and enforcing account security compliance (2FA/MFA)."
        freelance_title = "### **Freelance** — *Technical Translator & Localization Specialist (EN ↔ ES)*"
        freelance_sub = "*100% Remote | 2019 – 2023*"
        freelance_bullet = "- Executed bilingual technical translation and software localization for digital products and distributed engineering teams."
        edu_header = "## EDUCATION & CERTIFICATIONS"
        edu_1 = "- **B.S. in Computer & Information Engineering (Ingeniería en Computación e Informática)** — Universidad Andrés Bello (UNAB) (Graduated May 2026)"
        edu_2 = "- **Professional Degree in English–Spanish Translation & Interpreting** — Instituto Profesional EATRI (2015 – 2019)"
        edu_3 = "- **Programming Analyst Diploma (Analista Programador)** — INACAP (2010 – 2014)"
        certs = "- **Certifications:** **FinOps Certified Practitioner (FOCP)** *(FinOps Foundation / Linux Foundation — Certified, Nov 2026)*, **Google Cybersecurity Certificate**, **Google Data Analytics Certificate**, **IBM AI Developer Certificate**"
        langs = "- **Languages:** **English (C1 Certified — Fluent in live technical & vendor negotiations)**, **Spanish (Native)**"
        sec_skills = "## CORE TECHNICAL SKILLS"
        sec_summary = "## PROFESSIONAL SUMMARY"
    else:
        exp_header = "## EXPERIENCIA PROFESIONAL"
        role_header = "### **Colektia** — *Ingeniero DevSecOps & Cloud FinOps (May 2026 – Presente) | Previo: Ingeniero de Ciberseguridad, IA y Automatización (2024 – Abr 2026)*"
        role_sub = "*Santiago, Chile (100% Remoto) | 2024 – Presente*"
        trollbox_title = "### **Trollbox Agency** — *Especialista de Soporte & Escalamiento de Seguridad (Crypto)*"
        trollbox_sub = "*100% Remoto | 2021 – 2023*"
        trollbox_bullet = "- Soporte técnico bilingüe (EN/ES) y operaciones de seguridad para exchange de criptomonedas, mitigando vectores de fraude y aplicando controles de acceso y autenticación (2FA/MFA)."
        freelance_title = "### **Freelance** — *Traductor Técnico & Especialista en Localización (EN ↔ ES)*"
        freelance_sub = "*100% Remoto | 2019 – 2023*"
        freelance_bullet = "- Traducción técnica bilingüe y localización de software para productos digitales y equipos de ingeniería distribuidos."
        edu_header = "## EDUCACIÓN & CERTIFICACIONES"
        edu_1 = "- **Ingeniería en Computación e Informática** — Universidad Andrés Bello (UNAB) (Titulación: Mayo 2026)"
        edu_2 = "- **Traducción e Interpretación Inglés–Español** — Instituto Profesional EATRI (2015 – 2019)"
        edu_3 = "- **Analista Programador** — INACAP (2010 – 2014)"
        certs = "- **Certificaciones:** **FinOps Certified Practitioner (FOCP)** *(FinOps Foundation / Linux Foundation — Certificado, Nov 2026)*, **Google Cybersecurity Certificate**, **Google Data Analytics Certificate**, **IBM AI Developer Certificate**"
        langs = "- **Idiomas:** **Inglés (C1 Certificado — Negociaciones técnicas fluidas)**, **Español (Nativo)**"
        sec_skills = "## COMPETENCIAS TÉCNICAS PRINCIPALES"
        sec_summary = "## RESUMEN PROFESIONAL"

    ubicacion = DATOS_PERSONALES["ubicacion_en"] if idioma == "en" else DATOS_PERSONALES["ubicacion_es"]

    md_content = f"""---
tipo: cv
angulo: {angulo}
idioma: {idioma}
base: false
generado: '{hoy}'
---

# {DATOS_PERSONALES['nombre']}
**{titular}**
{ubicacion}
{DATOS_PERSONALES['contacto']}

---

{sec_summary}
{summary}

{sec_skills}
- {skills_p1}
- {skills_p2}
- {skills_p3}
- {skills_p4}

{exp_header}

{role_header}
{role_sub}
{bullets_md}

{trollbox_title}
{trollbox_sub}
{trollbox_bullet}

{freelance_title}
{freelance_sub}
{freelance_bullet}

{edu_header}
{edu_1}
{edu_2}
{edu_3}
{certs}
{langs}
"""
    return md_content


def main():
    parser = argparse.ArgumentParser(description="Ensamblador modular y determinista de CVs.")
    parser.add_argument("--oferta", help="ID o ruta del archivo de la oferta (ej. 2026-11-19--vtex-...)")
    parser.add_argument("--angulo", choices=["finops", "secops-siem", "ai-automation", "devsecops", "base"], default="finops")
    parser.add_argument("--idioma", choices=["en", "es"], default="en")
    parser.add_argument("--headline", help="Titular personalizado")
    parser.add_argument("--summary", help="Resumen ejecutivo personalizado")
    parser.add_argument("--output", help="Ruta del archivo Markdown de salida")
    parser.add_argument("--render", action="store_true", default=True, help="Renderizar HTML y PDF inmediatamente con render_cv.py")
    parser.add_argument("--dry-run", action="store_true")

    args = parser.parse_args()

    angulo = args.angulo
    idioma = args.idioma
    slug_oferta = "felipe-vergara"

    # Si se pasa una oferta, leer su frontmatter
    if args.oferta:
        oferta_path = args.oferta
        if not os.path.exists(oferta_path):
            # Resolver ID o legacy_id vía estado.py listar --json
            res = subprocess.run([sys.executable, "scripts/estado.py", "listar", "--json"], capture_output=True, text=True, cwd=ROOT)
            if res.returncode == 0 and res.stdout.strip():
                for o in json.loads(res.stdout):
                    if args.oferta in (o.get("id"), o.get("legacy_id"), o.get("archivo")):
                        oferta_path = os.path.join(ROOT, o["archivo"])
                        break

        if os.path.exists(oferta_path):
            with open(oferta_path, "r", encoding="utf-8") as f:
                parts = f.read().split("---", 2)
                if len(parts) >= 3:
                    data = yaml.safe_load(parts[1]) or {}
                    angulo = data.get("angulo") or angulo
                    idioma_str = str(data.get("idioma", "")).lower()
                    idioma = "es" if "español" in idioma_str and "inglés" not in idioma_str else "en"
                    # slug de oferta
                    basename = os.path.basename(oferta_path).replace(".md", "")
                    slug_oferta = f"felipe-vergara-{basename}"

    hoy = datetime.date.today().strftime("%Y-%m-%d")
    out_dir = os.path.join(ROOT, "cvs-generados", hoy)
    os.makedirs(out_dir, exist_ok=True)

    out_file = args.output or os.path.join(out_dir, f"cv-{slug_oferta}.md")

    cv_md = ensamblar_cv(
        angulo=angulo,
        idioma=idioma,
        custom_headline=args.headline,
        custom_summary=args.summary
    )

    if args.dry_run:
        print(f"[DRY-RUN] Se generaría CV en: {out_file} (Ángulo: {angulo}, Idioma: {idioma})")
        print("\n" + cv_md[:350] + "\n...")
        return

    with open(out_file, "w", encoding="utf-8") as f:
        f.write(cv_md)
    print(f"✓ CV ensamblado en Markdown (0 tokens): {out_file}")

    # Renderizar a HTML y PDF
    if args.render and os.path.exists(RENDER_SCRIPT):
        print(f"⚙ Renderizando HTML y PDF con render_cv.py...")
        res = subprocess.run([sys.executable, RENDER_SCRIPT, out_file], cwd=ROOT)
        if res.returncode == 0:
            print(f"✓ HTML y PDF generados correctamente.")
        else:
            print(f"⚠ Aviso: Falló renderizado de PDF (puede requerir Chrome/Chromium).")

    # Si se especificó oferta, asociar CV con estado.py mover
    if args.oferta:
        rel_cv = os.path.relpath(out_file, ROOT)
        print(f"⚙ Vinculando CV a oferta con estado.py mover...")
        subprocess.run([sys.executable, "scripts/estado.py", "mover", args.oferta, "cv_generado", "--cv", rel_cv], cwd=ROOT)


if __name__ == "__main__":
    main()
