---
description: Flujo completo: tracker → búsqueda → CVs → brechas → resumen
---

# /corrida-diaria

1. Lee `AGENTS.md` (reglas globales, protocolo §2 y **Modo Orquestador en Google Antigravity** §3) y `agentes.yaml`.
2. Actúa como **Orquestador**: ejecuta primero los pasos a 0 tokens (`estado.py validar`, `scripts/rastrear_ats.py --buscar --crear-ofertas` si aplica) y delega cada rol LLM de `AGENTS.md` §4 mediante `invoke_subagent` (`TypeName: "self"`) usando el `Model` exacto de `agentes.yaml → roles.<rol>.modelos.antigravity`:
   - `perfil-tracker` → `flash_lite` (Flujo A y Flujo B)
   - `buscador-ofertas` → `flash` (búsqueda web complementaria y rúbrica)
   - `adaptador-cv` → `flash` (o `scripts/ensamblar_cv.py` a 0 tokens + material en subagente `flash`)
3. Cada subagente lee su `.agents/skills/<rol>/SKILL.md`, respeta la propiedad de archivos (§2.4) y reporta sus resultados al orquestador.
4. Cierra con `python3 scripts/estado.py validar` y `python3 scripts/estado.py sincronizar`, y entrega el resumen ejecutivo final.
