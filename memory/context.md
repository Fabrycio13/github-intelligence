# GitHub Intelligence

Project codebase with persistent memory active.

## Memory Status
- **Core Memory:** Active in `memory/` (`decisions/`, `errors/`, `tasks/`).
- **Architecture:** Initial inventory mapped (88 files scanned, 31 implementation files).
- **Setup Status (PC de trabalho - 20/10/2026):**
  - Python / `.venv`: Sincronizado via `uv sync --locked`.
  - Suíte local: 768 testes passando, PostgreSQL 17.11 nativo portátil configurado.
  - Supabase: Conectado via pooler IPv4 (`aws-0-us-east-1.pooler.supabase.com`), TLS `verify-full` com CA e senha DPAPI.
  - GitHub App: Dev Intelligence Fabrycio13 (ID: 5380709) configurada com chave DPAPI protegida; acesso validado para `Fabrycio13/project-mds` e `Fabrycio13/skills`.
  - Configurações locais ativas em `config/*.local.json` e `config/repositories.json`.
