# Arquitectura del agente

Fuentes de los diagramas: `docs/diagramas/*.mmd` (Mermaid). También hay versiones PNG en la misma carpeta.

Principio: **primero lo determinista, el LLM al final**. La búsqueda y todos los filtros son código
(sin LLM); el LLM (Claude u otro modelo vía OpenRouter, según `llm` en `config.yaml`) solo evalúa el encaje de las ofertas que sobreviven y, cuando hace falta, la hoja de vida.

## Diagrama de componentes

Arquitectura hexagonal: los casos de uso (`application/`) y el dominio (`domain/`) solo conocen los
**puertos** (`application/ports.py`). Cada herramienta externa entra por un **adaptador** en
`adapters/`, y `bootstrap.py` decide qué adaptador implementa cada puerto.

```mermaid
flowchart LR
    GHA["launchd (macOS)<br/>3 veces al día"]
    CLI["entrypoints/cli.py<br/>adaptador de entrada"]
    BOOT["bootstrap.py<br/>raíz de composición<br/>(lee config.yaml y conecta<br/>cada puerto con su adaptador)"]

    subgraph CORE["Núcleo · sin dependencias de infraestructura"]
        direction TB
        subgraph APP["application/ · casos de uso"]
            direction TB
            RSC["RunSearchCycle"]
            EP["EnsureProfile"]
        end
        subgraph DOM["domain/"]
            direction TB
            MOD["models<br/>Profile · JobPosting · JobMatch<br/>TailoredResume · ResumeVersion"]
            POL["policies (deterministas)<br/>SearchPreferences → plan · JobFilter<br/>job_key · duplicate_signature<br/>MatchingPolicy · ReusePolicy"]
        end
    end

    subgraph PORTS["application/ports.py · puertos"]
        direction TB
        P_CAND(["Datos del candidato<br/>ResumeSource · CodeRepositoryReader"])
        P_JOB(["Mercado laboral<br/>JobSource"])
        P_LLM(["Razonamiento<br/>ProfileInferer · JobMatcher<br/>ResumeSelector · ResumeTailor"])
        P_STO(["Almacenamiento<br/>ProfileStore · ApplicationStore<br/>SeenJobsRepository · MatchHistory"])
        P_NOT(["Avisos<br/>Notifier"])
    end

    subgraph ADAPTERS["adapters/ · adaptadores de salida"]
        direction TB
        A_CAND["resume/FileResumeSource<br/>code_repositories/GitRepositoryReader"]
        A_JOB["job_sources/ LinkedInMcpJobSource<br/>llama search_jobs y get_job_details<br/>+ parser de texto (sin LLM)"]
        A_LLM["llm/ LlmProfileInferer · LlmJobMatcher<br/>LlmResumeSelector · LlmResumeTailor<br/>sobre Anthropic u OpenRouter"]
        A_STO["persistence/ JSON y archivos<br/>+ resume/markdown_renderer (MD→HTML→PDF)"]
        A_NOT["notifications/ TelegramNotifier<br/>ConsoleNotifier (dry-run)"]
    end

    subgraph EXT["Sistemas externos"]
        direction TB
        E_CAND["resume/base.md<br/>GitHub y remotos git"]
        E_JOB["Servidor MCP de LinkedIn"]
        E_LLM["API de Anthropic (Claude)<br/>u OpenRouter (cualquier modelo)"]
        E_STO[("Disco local: data/ · output/<br/>(fuera de git)")]
        E_NOT["Telegram"]
    end

    GHA --> CLI --> APP
    CLI --> BOOT
    BOOT -. crea .-> APP
    BOOT -. instancia .-> ADAPTERS
    RSC --> EP
    APP --> DOM
    APP --> PORTS

    P_CAND -. implementado por .- A_CAND
    P_LLM -. implementado por .- A_LLM
    P_JOB -. implementado por .- A_JOB
    P_STO -. implementado por .- A_STO
    P_NOT -. implementado por .- A_NOT

    A_CAND --> E_CAND
    A_LLM --> E_LLM
    A_JOB --> E_JOB
    A_STO --> E_STO
    A_NOT --> E_NOT
```

| Puerto | Para qué lo usa el núcleo | Adaptador actual |
|---|---|---|
| `ResumeSource` | leer tu CV base | `FileResumeSource` (`resume/base.md`, .txt o .pdf) |
| `CodeRepositoryReader` | listar repos y extraer evidencia | `GitRepositoryReader` (GitHub + cualquier remoto git) |
| `ProfileInferer` | inferir el perfil | `LlmProfileInferer` |
| `JobSource` | buscar ofertas (determinista) | `LinkedInMcpJobSource` (herramientas del servidor MCP llamadas directamente) |
| `JobMatcher` | puntuar cada oferta | `LlmJobMatcher` |
| `ResumeSelector` | decidir reutilizar / adaptar / crear | `LlmResumeSelector` |
| `ResumeTailor` | crear o adaptar la hoja de vida | `LlmResumeTailor` |

Los cuatro adaptadores `Llm*` comparten los prompts (`adapters/llm/prompts.py`) y delegan el transporte en un
`StructuredModel`: `AnthropicStructuredModel` o `OpenRouterStructuredModel`, elegido por `llm.provider`.
| `ProfileStore` | guardar el perfil | `JsonProfileStore` (`data/profile.json`) |
| `ApplicationStore` | catálogo de hojas de vida | `FileSystemApplicationStore` (`output/…/version.json`) |
| `SeenJobsRepository` | no repetir ofertas | `JsonSeenJobsRepository` (`data/state.json`) |
| `MatchHistory` | historial de puntajes | `JsonlMatchHistory` (`data/matches.jsonl`) |
| `Notifier` | avisarte | `TelegramNotifier`, `ConsoleNotifier` (dry-run) |

## Diagrama de secuencia: un ciclo completo

Lo que pasa en cada una de las 3 ejecuciones diarias que lanza launchd en tu Mac (`scripts/macos/run_local.sh`).

```mermaid
sequenceDiagram
    autonumber
    actor Cron as launchd (macOS)
    participant CLI as CLI
    participant RSC as RunSearchCycle
    participant EP as EnsureProfile
    participant Store as Disco local (data/, output/)
    participant Src as JobSource (LinkedIn vía MCP)
    participant LLM as LLM (matcher, selector, tailor)
    participant Notif as Notifier (Telegram)
    actor User as Tú

    Cron->>CLI: run_local.sh → python -m job_agent run
    CLI->>RSC: execute()

    rect rgba(127,127,127,0.08)
    Note over RSC,EP: 1. Perfil
    RSC->>EP: execute()
    EP->>Store: leer CV base y heads de los repos
    alt CV y repos sin cambios y perfil vigente
        Store-->>EP: perfil guardado
    else algo cambió o el perfil es viejo
        EP->>LLM: inferir perfil (CV + evidencia de repos)
        LLM-->>EP: Profile
        EP->>Store: guardar data/profile.json
    end
    EP-->>RSC: Profile
    end

    rect rgba(127,127,127,0.08)
    Note over RSC,Src: 2. Búsqueda determinista (sin LLM)
    RSC->>RSC: plan = palabras clave × ubicaciones
    loop cada fuente configurada
        RSC->>Src: collect(plan, admit, presupuesto)
        Src->>Src: search_jobs por consulta (filtros de LinkedIn: fecha, modalidad, nivel)
        loop cada id encontrado
            Src->>RSC: admit(id)
            RSC->>Store: ¿id ya procesado?
            RSC-->>Src: sí: se descarta sin pedir detalle / no: se admite
        end
        Src->>Src: get_job_details solo de los admitidos y parseo del texto
        Src-->>RSC: ofertas nuevas (o error, sin detener las demás)
    end
    end

    rect rgba(127,127,127,0.08)
    Note over RSC,Store: 3. Filtros deterministas
    RSC->>RSC: empresa excluida, palabra excluida en el título, modalidad, antigüedad, sin descripción
    RSC->>Store: ¿misma empresa + cargo ya procesado con otro id?
    RSC->>RSC: duplicadas dentro de la corrida
    RSC->>Store: marcar descartadas (con el motivo) para no volver a pedirlas
    end

    loop cada oferta que pasó los filtros (máx. max_jobs_per_run)
        rect rgba(127,127,127,0.08)
        Note over RSC,LLM: 4. Afinidad (único juicio del LLM sobre la oferta)
        RSC->>LLM: score(oferta, perfil, CV)
        LLM-->>RSC: JobMatch (puntaje, motivos, brechas, resume_undersells)
        end

        opt puntaje ≥ umbral y el CV no te hace justicia
            rect rgba(127,127,127,0.08)
            Note over RSC,Store: 5. Hoja de vida
            RSC->>Store: versiones del catálogo (mismo CV base)
            alt hay versiones candidatas
                RSC->>LLM: choose(oferta, candidatas)
                LLM-->>RSC: reuse | adapt | create
            end
            alt reuse
                RSC->>Store: registrar uso y ubicar la versión
            else adapt
                RSC->>Store: cargar versión de partida
                RSC->>LLM: tailor(..., starting_from)
                LLM-->>RSC: TailoredResume (cambios mínimos)
                RSC->>Store: guardar versión nueva (adapted_from)
            else create (o sin candidatas)
                RSC->>LLM: tailor(oferta, perfil, CV base)
                LLM-->>RSC: TailoredResume
                RSC->>Store: guardar resume.md/html/pdf + README + version.json
            end
            end
        end

        opt puntaje ≥ umbral de notificación
            RSC->>Notif: notify(alerta)
            Notif->>User: mensaje + PDF por Telegram
        end
        RSC->>Store: marcar como puntuada, historial (matches.jsonl)
    end

    RSC-->>CLI: CycleReport
    CLI-->>Cron: fin
```

Notas:

- El perfil (pasos 3 a 9) solo se recalcula con Claude cuando cambió tu CV, cambió algún repo o el
  perfil tiene más de `profile_refresh_days` días.
- Una oferta ya procesada (puntuada o descartada por un filtro) nunca se vuelve a descargar: su id se
  rechaza antes de pedir el detalle. Su fecha de "última vista" se renueva cada vez que aparece, y solo
  se olvida tras 90 días sin aparecer en ninguna búsqueda.
- Los presupuestos (`max_details_per_run`, `max_jobs_per_run`) se aplican **después** de descartar lo
  conocido, así que las ofertas repetidas no ocupan cupo.
- Si falla una fuente, una consulta o el detalle de una oferta, se registra y el resto continúa. Una
  oferta que no se pudo puntuar no se marca, y se reintenta en la siguiente corrida.

## Diagrama de secuencia: búsqueda en LinkedIn

Detalle de la búsqueda determinista con `LinkedInMcpJobSource`.

```mermaid
sequenceDiagram
    autonumber
    participant RSC as RunSearchCycle
    participant Seen as Ofertas vistas (data/state.json)
    participant LI as LinkedInMcpJobSource
    participant MCP as Servidor MCP de LinkedIn
    participant Claude as LLM (JobMatcher)

    RSC->>RSC: plan = (extra_keywords + cargos + keywords del perfil) × ubicaciones
    RSC->>LI: collect(plan, admit, max_details_per_run)
    LI->>MCP: lanzar por stdio e initialize()
    loop cada consulta del plan (máx. max_queries)
        LI->>MCP: search_jobs(keywords, location, date_posted, work_type, experience_level, sort_by=date)
        MCP-->>LI: job_ids
    end
    LI->>LI: unir ids sin repetir (orden: más recientes primero)
    loop cada id, hasta agotar el presupuesto de detalles
        LI->>RSC: admit(linkedin:id)
        RSC->>Seen: ¿id conocido? (y renueva su fecha de última vista)
        alt ya procesado o repetido en esta corrida
            RSC-->>LI: no (no se pide el detalle)
        else nuevo
            RSC-->>LI: sí
        end
    end
    loop cada id admitido
        LI->>MCP: get_job_details(job_id)
        MCP-->>LI: texto de la oferta
        LI->>LI: parsear empresa, cargo, ubicación, antigüedad, modalidad, descripción
    end
    LI->>MCP: cerrar sesión
    LI-->>RSC: ofertas nuevas
    RSC->>RSC: JobFilter + duplicados por empresa y cargo normalizados
    RSC->>Seen: guardar descartadas con su motivo
    loop cada candidata (máx. max_jobs_per_run)
        RSC->>Claude: score(oferta, perfil, CV)
        Claude-->>RSC: JobMatch
    end
```

### Filtros, en orden

| Etapa | Dónde | Regla |
|---|---|---|
| 1 | LinkedIn (`search_jobs`) | antigüedad (`posted_within_days`), modalidad (`work_types`), nivel (`experience_levels`), orden por fecha |
| 2 | Agente, antes de pedir detalle | id ya procesado en corridas anteriores o repetido en esta corrida |
| 3 | Agente, sobre el detalle (`JobFilter`) | empresa excluida (normalizada: "Acme Inc." = "ACME"), palabra excluida en el título (palabra completa), modalidad no deseada, publicación antigua, sin descripción |
| 4 | Agente, sobre el detalle | misma empresa + cargo normalizados que otra oferta de esta corrida o ya procesada con otro id (reposts) |
| 5 | LLM | encaje con tu perfil: puntaje 0-100 |

El texto de cada oferta se convierte en campos con un parser determinista
(`adapters/job_sources/linkedin_text.py`). Si no reconoce el formato, deja vacíos título, empresa o
ubicación: esos filtros no se aplican a esa oferta (no se descarta por falta de datos) y, para mostrarla
en la notificación, se usa lo que Claude leyó en el texto al puntuarla.
