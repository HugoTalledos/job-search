# Arquitectura del agente

Fuentes de los diagramas: `docs/diagramas/*.mmd` (Mermaid). También hay versiones PNG en la misma carpeta.

## Diagrama de componentes

Arquitectura hexagonal: los casos de uso (`application/`) y el dominio (`domain/`) solo conocen los
**puertos** (`application/ports.py`). Cada herramienta externa entra por un **adaptador** en
`adapters/`, y `bootstrap.py` decide qué adaptador implementa cada puerto.

```mermaid
flowchart LR
    GHA["GitHub Actions<br/>cron 3 veces al día"]
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
            POL["policies<br/>MatchingPolicy · ReusePolicy<br/>SearchPreferences · job_key"]
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
        A_JOB["job_sources/ McpJobSource (uno por servidor)<br/>WebSearchJobSource<br/>→ claude_search_agent"]
        A_LLM["llm/ ClaudeProfileInferer · ClaudeJobMatcher<br/>ClaudeResumeSelector · ClaudeResumeTailor"]
        A_STO["persistence/ JSON y archivos<br/>+ resume/markdown_renderer (MD→HTML→PDF)"]
        A_NOT["notifications/ TelegramNotifier<br/>ConsoleNotifier (dry-run)"]
    end

    subgraph EXT["Sistemas externos"]
        direction TB
        E_CAND["resume/base.md<br/>GitHub y remotos git"]
        E_JOB["Servidor MCP de LinkedIn<br/>(+ otros MCP, búsqueda web)"]
        E_LLM["API de Anthropic (Claude)"]
        E_STO[("Repo: data/ · output/")]
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
    A_JOB -- el agente razona con --> E_LLM
    A_STO --> E_STO
    A_NOT --> E_NOT
```

| Puerto | Para qué lo usa el núcleo | Adaptador actual |
|---|---|---|
| `ResumeSource` | leer tu CV base | `FileResumeSource` (`resume/base.md`, .txt o .pdf) |
| `CodeRepositoryReader` | listar repos y extraer evidencia | `GitRepositoryReader` (GitHub + cualquier remoto git) |
| `ProfileInferer` | inferir el perfil | `ClaudeProfileInferer` |
| `JobSource` | buscar ofertas | `McpJobSource` (uno por servidor MCP), `WebSearchJobSource` |
| `JobMatcher` | puntuar cada oferta | `ClaudeJobMatcher` |
| `ResumeSelector` | decidir reutilizar / adaptar / crear | `ClaudeResumeSelector` |
| `ResumeTailor` | crear o adaptar la hoja de vida | `ClaudeResumeTailor` |
| `ProfileStore` | guardar el perfil | `JsonProfileStore` (`data/profile.json`) |
| `ApplicationStore` | catálogo de hojas de vida | `FileSystemApplicationStore` (`output/…/version.json`) |
| `SeenJobsRepository` | no repetir ofertas | `JsonSeenJobsRepository` (`data/state.json`) |
| `MatchHistory` | historial de puntajes | `JsonlMatchHistory` (`data/matches.jsonl`) |
| `Notifier` | avisarte | `TelegramNotifier`, `ConsoleNotifier` (dry-run) |

## Diagrama de secuencia: un ciclo completo

Lo que pasa en cada una de las 3 ejecuciones diarias (`python -m job_agent run`).

```mermaid
sequenceDiagram
    autonumber
    actor Cron as GitHub Actions
    participant CLI as CLI
    participant RSC as RunSearchCycle
    participant EP as EnsureProfile
    participant Store as Persistencia (data/, output/)
    participant Src as JobSource (LinkedIn MCP, web)
    participant LLM as Claude (matcher, selector, tailor)
    participant Notif as Notifier (Telegram)
    actor User as Tú

    Cron->>CLI: python -m job_agent run
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
    Note over RSC,Src: 2. Búsqueda
    RSC->>Store: URLs ya vistas
    RSC->>RSC: armar SearchCriteria (roles, keywords, ubicaciones)
    loop cada fuente configurada
        RSC->>Src: search(criteria)
        Src-->>RSC: ofertas (o error, sin detener las demás)
    end
    RSC->>RSC: quitar duplicadas, vistas y empresas excluidas
    end

    loop cada oferta nueva (máx. max_jobs_per_run)
        rect rgba(127,127,127,0.08)
        Note over RSC,LLM: 3. Afinidad
        RSC->>LLM: score(oferta, perfil, CV)
        LLM-->>RSC: JobMatch (puntaje, motivos, brechas, resume_undersells)
        end

        opt puntaje ≥ umbral y el CV no te hace justicia
            rect rgba(127,127,127,0.08)
            Note over RSC,Store: 4. Hoja de vida
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
        RSC->>Store: marcar como vista, historial (matches.jsonl)
    end

    RSC-->>CLI: CycleReport
    CLI-->>Cron: fin
    Cron->>Store: git commit + push de data/ y output/
```

Notas:

- Los pasos 3 a 9 solo llaman a Claude cuando cambió tu CV, cambió algún repo o el perfil tiene más
  de `profile_refresh_days` días.
- Si una fuente falla (paso 13), el error queda en el reporte y las demás fuentes siguen.
- Una oferta se marca como vista solo si se pudo puntuar; si falla el puntaje, se reintenta en el
  siguiente ciclo.
- Si falla la generación de la hoja de vida, igual se envía la notificación, indicando que no se generó.

## Diagrama de secuencia: búsqueda en una fuente MCP (LinkedIn)

Detalle del paso 12 para `McpJobSource`. `WebSearchJobSource` sigue el mismo bucle, pero la
herramienta es la búsqueda web que se ejecuta en los servidores de Anthropic.

```mermaid
sequenceDiagram
    autonumber
    participant RSC as RunSearchCycle
    participant MJS as McpJobSource
    participant MCP as Servidor MCP (LinkedIn)
    participant Agent as claude_search_agent
    participant Claude as API de Anthropic

    RSC->>MJS: search(criteria)
    MJS->>MCP: lanzar por stdio e initialize()
    MJS->>MCP: list_tools()
    MCP-->>MJS: search_jobs, get_job_details, ...
    MJS->>Agent: run(herramientas MCP + submit_jobs, criteria)
    loop hasta que Claude llame submit_jobs (máx. 40 turnos)
        Agent->>Claude: mensajes + herramientas
        Claude-->>Agent: tool_use (p. ej. search_jobs "python backend", "Remote")
        Agent->>MCP: call_tool(...)
        MCP-->>Agent: resultados de LinkedIn
        Agent->>Claude: tool_result
    end
    Claude-->>Agent: tool_use submit_jobs(jobs)
    Agent->>Agent: validar cada JobPosting
    Agent-->>MJS: lista de ofertas
    MJS->>MCP: cerrar sesión
    MJS-->>RSC: ofertas
```
