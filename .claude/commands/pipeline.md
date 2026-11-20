---
description: 'Director: pipeline extremo a extremo con subagentes (buscar → CVs en paralelo → material → sincronizar → Drive)'
argument-hint: '[foco: finops | devsecops | secops-siem | ai-automation] [max N ofertas] [sin-busqueda] [drive]'
---

Eres el **director** del pipeline de career-agent. No haces el trabajo de los roles: lo delegas y verificas los hand-offs a través del estado en archivos (`AGENTS.md` §2). Lee `AGENTS.md` antes de empezar.

Argumentos: $ARGUMENTS

Ejecuta sin pedir confirmación entre etapas:
1. `python3 scripts/estado.py validar`. Si hay errores, detente y repórtalos.
2. Guarda la lista de IDs actual (`python3 scripts/estado.py listar --json`) para detectar ofertas nuevas al final.
3. Salvo `sin-busqueda`: delega al subagente `buscador-ofertas` la búsqueda (con el foco y el tope de ofertas de los argumentos, si vienen).
4. Ejecuta `python3 scripts/estado.py listar --prioridad alta --sin-cv --json`. Lanza **en paralelo, en un solo mensaje**, un subagente `adaptador-cv` por oferta (máximo 3 a la vez), pasando el `id` de la oferta. Cada uno genera CV y material de postulación.
5. Verifica cada hand-off: la oferta debe quedar en `cv_generado` con `cv` y `material`. Reintenta una vez las que fallen.
6. `python3 scripts/estado.py sincronizar`. Si viene `drive`: `python3 scripts/drive_sync.py` (si rclone no está configurado, avísalo y sigue).
7. Resumen ejecutivo: ofertas nuevas por prioridad, CVs y material generados (rutas), links para postular, errores.

Los subagentes ya tienen asignado su modelo en `.claude/agents/*.md` (según `agentes.yaml`); no lo cambies.
Alternativa multi-herramienta fuera de esta sesión: `python3 scripts/pipeline.py` (lanza cada rol en Claude Code o Cursor según `agentes.yaml`).
