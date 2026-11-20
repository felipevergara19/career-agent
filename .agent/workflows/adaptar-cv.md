---
description: Genera o reutiliza el CV adaptado (md/html/pdf) para una oferta
---

# /adaptar-cv

1. Lee `AGENTS.md` (protocolo §2 y Modo Orquestador §3) y `agentes.yaml`.
2. Si solo se necesita ensamblar un CV estándar por ángulo a 0 tokens, ejecuta `python3 scripts/ensamblar_cv.py --oferta <id>`.
3. Si se requiere personalización profunda ATS y/o material de postulación, delega mediante `invoke_subagent` (`TypeName: "self"`, `Role: "Adaptador de CV"`, `Model: <agentes.yaml → roles.adaptador-cv.modelos.antigravity>`, o varios en paralelo si hay múltiples ofertas). El subagente lee `.agents/skills/adaptador-cv/SKILL.md`, ejecuta el procedimiento completo y reporta al orquestador.
4. Argumentos ([id-oferta | legacy_id]): usa lo que Felipe escribió después de `/adaptar-cv` (sin argumentos, toma `python3 scripts/estado.py listar --prioridad alta --sin-cv`).
5. Verifica con `python3 scripts/estado.py validar` y `python3 scripts/estado.py sincronizar`, y entrega el resumen final.
