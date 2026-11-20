---
description: Búsqueda diaria de ofertas 100% remotas con rúbrica 0–100
---

# /buscar

1. Lee `AGENTS.md` (protocolo §2 y Modo Orquestador §3) y `agentes.yaml`.
2. Prioridad 0 tokens: si aplica, ejecuta primero `python3 scripts/rastrear_ats.py --buscar --crear-ofertas` (con `--foco` si se indicó).
3. Para la búsqueda web y evaluación con rúbrica, delega mediante `invoke_subagent` (`TypeName: "self"`, `Role: "Buscador de Ofertas"`, `Model: <agentes.yaml → roles.buscador-ofertas.modelos.antigravity>`). El subagente debe leer `.agents/skills/buscador-ofertas/SKILL.md`, ejecutar Pasos 1–5 (respetando el foco opcional si se indicó) y reportar los resultados al orquestador.
4. Argumentos ([foco opcional: finops | devsecops | secops-siem | ai-automation]): usa lo que Felipe escribió después de `/buscar`.
5. Verifica con `python3 scripts/estado.py validar` y `python3 scripts/estado.py sincronizar`, y entrega el resumen final.
