---
description: Guía de preparación de entrevista para una oferta
---

# /entrevista

1. Lee `AGENTS.md` (protocolo §2 y Modo Orquestador §3) y `agentes.yaml`.
2. Delega mediante `invoke_subagent` (`TypeName: "self"`, `Role: "Preparador de Entrevistas"`, `Model: <agentes.yaml → roles.preparador-entrevistas.modelos.antigravity>`). El subagente lee `.agents/skills/preparador-entrevistas/SKILL.md`, genera la guía (o debrief si Felipe cuenta cómo le fue) y reporta al orquestador.
3. Argumentos (<id-oferta | legacy_id> [hr | tecnica | cierre]): usa lo que Felipe escribió después de `/entrevista`.
4. Verifica con `python3 scripts/estado.py validar` y `python3 scripts/estado.py sincronizar`, y entrega el resumen final.
