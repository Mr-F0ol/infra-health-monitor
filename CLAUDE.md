## ROADMAP DETALHADO

### FASE 1 — Core de checagens + persistência
Entrega: o coração do sistema.
- Classe base de checagem (CheckBase) + Enum HealthStatus (HEALTHY/DEGRADED/DOWN).
- Checagens: HTTP (status code + latência), TCP (porta aberta), System (CPU/memória/disco via psutil).
- Persistência de cada resultado no PostgreSQL via SQLAlchemy, com timestamp.
- Config via pydantic-settings + .env.
- Endpoint FastAPI GET /health rodando as checagens sob demanda.
- Testes dos três tipos de checagem (com mocks).
- docker-compose subindo PostgreSQL local.

### FASE 2 — Agendamento automático
Entrega: o monitor passa a trabalhar sozinho.
- Integração do APScheduler para rodar checagens em intervalos configuráveis por serviço.
- Definição de serviços monitorados via arquivo de config (YAML ou JSON): nome, tipo, alvo, intervalo, limiares.
- Loop de agendamento iniciado junto com a aplicação FastAPI (lifespan).
- Resultados gravados automaticamente no banco a cada execução.
- Endpoint GET /services listando serviços e seus estados atuais.
- Endpoint GET /history?service=X retornando histórico de um serviço.
- Testes do scheduler (verificar que jobs são registrados e executados).

### FASE 3 — Alertas com deduplicação
Entrega: o sistema te avisa quando algo cai.
- Camada de notificação com providers plugáveis: Discord (webhook) e Telegram (bot).
- Lógica de transição de estado: alerta dispara só quando muda HEALTHY→DOWN (ou volta DOWN→HEALTHY), nunca repetido.
- Redis para guardar o último estado conhecido de cada serviço (deduplicação) e evitar spam de alertas.
- Mensagens de alerta com contexto: serviço, estado, latência, timestamp.
- Alerta de recuperação quando o serviço volta ao normal.
- Config de quais canais usar via .env.
- Testes da lógica de deduplicação e dos providers (mockados).

### FASE 4 — Métricas e dashboard
Entrega: observabilidade visual de nível profissional.
- Exposição de métricas no formato Prometheus (endpoint /metrics) usando prometheus-client.
- Métricas: status atual por serviço (gauge), latência (histogram), total de checagens (counter), total de falhas.
- docker-compose estendido com Prometheus (scrape do /metrics) e Grafana.
- Dashboard Grafana provisionado: uptime por serviço, latência ao longo do tempo, contagem de falhas.
- Provisionamento do dashboard e datasource como código (arquivos de config versionados).

### FASE 5 — CI/CD e qualidade
Entrega: prova de que você sabe entregar software, não só escrevê-lo.
- GitHub Actions: workflow que roda em cada push/PR.
- Pipeline: lint (ruff), checagem de tipos (mypy), testes (pytest) com relatório de cobertura.
- Build da imagem Docker no CI.
- Badge de status e de cobertura no README.
- README completo: descrição, arquitetura (diagrama), como rodar, screenshots do dashboard, decisões técnicas.
- Opcional: docker-compose de produção e instruções de deploy.