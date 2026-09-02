# DataWorkbench VM deployment

This package runs the React frontend and FastAPI backend with Docker Compose.
Nginx serves the frontend and proxies `/api` to the backend. A named Docker
volume preserves the SQLite database, uploads, knowledge-base files, and
generated analysis artifacts.

## Prerequisites

- A Linux VM with Docker Engine and the Docker Compose plugin
- Inbound TCP access to port 80, or the port selected with `HTTP_PORT`

## Start

1. Extract the archive and enter its directory.
2. Configure these variables in the VM shell, service manager, or secret store:
   `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`,
   `AZURE_OPENAI_DEPLOYMENT`, and `AZURE_OPENAI_API_VERSION`.
3. Optionally set `HTTP_PORT`; it defaults to `80`.
4. Run `docker compose up -d --build` from the configured shell.
5. Check it with `docker compose ps` and `docker compose logs --tail=100`.

Open `http://VM_IP/` in a browser. No `.env` file is included in this package.

Stop without deleting data with `docker compose down`. Update after replacing
the application files with `docker compose up -d --build`. Do not run
`docker compose down -v` unless you intentionally want to delete persistent
application data.
