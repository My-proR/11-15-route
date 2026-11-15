# Monitoring
API exposes Prometheus metrics at `/metrics` (protect with `METRICS_TOKEN`). Suggested alerts: 5xx rate > 1% for 5 min; p95 latency > 1 s; `/health/ready` failing; notification outbox oldest pending age > 10 min; failed deliveries rising. Scrape example:
```yaml
scrape_configs:
  - job_name: routepilot-api
    metrics_path: /metrics
    authorization: { credentials: "<METRICS_TOKEN>" }
    static_configs: [{ targets: ["api:8000"] }]
```
Untested scrape config; adjust to the metric names actually exposed (`curl /metrics`).
