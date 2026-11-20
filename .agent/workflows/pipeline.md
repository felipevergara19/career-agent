---
description: 'Director: pipeline extremo a extremo (buscar → CVs → material → sincronizar → Drive)'
---

# /pipeline

1. Lee `AGENTS.md` (reglas globales, protocolo §2 y **Modo Orquestador en Google Antigravity** §3) y `agentes.yaml`. Eres el **Orquestador (`director`)**: delegas cada etapa con LLM a un subagente más barato vía `invoke_subagent` (`Model` de `agentes.yaml → modelos.antigravity`) y verificas los hand-offs en archivos.
2. Ejecuta `python3 scripts/estado.py validar`. Si hay errores, detente y repórtalos.
3. Salvo que Felipe escriba `sin-busqueda` después de `/pipeline`:
   - Primero intenta rastreo directo a 0 tokens (`python3 scripts/rastrear_ats.py --buscar --crear-ofertas`).
   - Luego, si se requiere búsqueda web abierta, delega en un subagente `buscador-ofertas` (`Model: flash`) que lea `.agents/skills/buscador-ofertas/SKILL.md`.
4. Ejecuta `python3 scripts/estado.py listar --prioridad alta --sin-cv`. Para cada oferta, prioriza `python3 scripts/ensamblar_cv.py --oferta <id>` (0 tokens) o delega en subagentes `adaptador-cv` en paralelo (`Model: flash`) para generar CV personalizado + material de postulación.
5. Verifica que cada oferta quede en `cv_generado` con `cv` (y `material` si aplica).
6. Ejecuta `python3 scripts/estado.py sincronizar` y, si Felipe escribió `drive`, `python3 scripts/drive_sync.py`.
7. Entrega el resumen ejecutivo: ofertas nuevas, CVs, material y links para postular.

Alternativa CLI determinista: `python3 scripts/pipeline.py --help` (0 tokens por defecto; `--web` y `--llm-cv` usan Claude Code / Cursor según `agentes.yaml`).
