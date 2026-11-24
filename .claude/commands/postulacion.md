---
name: postulacion
description: Responde preguntas de formularios y portales de empleo para una oferta con veracidad estricta basada en el Perfil Maestro
args:
  - name: oferta_id
    description: ID o slug de la oferta (ej. 2026-11-23--airtm-infrastructure-engineer)
    required: true
  - name: preguntas
    description: Preguntas del portal a responder (opcional; si se omite, se piden interactivamente)
    required: false
---

Ejecuta el asistente de postulación:

```bash
python3 scripts/asistente_postulacion.py --oferta $oferta_id ${preguntas:+--preguntas "$preguntas"}
```

El script utiliza el Perfil Maestro (`perfil/perfil-maestro.md`), preferencias salariales y logros del grafo para redactar respuestas de alto impacto listas para copiar al portal, guardándolas en `postulaciones/material/<oferta_id>.md`.
