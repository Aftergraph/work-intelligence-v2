# Aftergraph Work Intelligence V2 — Deployment Guide

## Quick Start (Local Development)

### 1. Clone and Setup

```bash
git clone https://github.com/Aftergraph/work-intelligence-v2.git
cd work-intelligence-v2

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# or
.venv\Scripts\activate  # Windows

# Install dependencies
pip install -e ".[dev]"
```

### 2. Run Tests

```bash
# Unit tests only
pytest tests/ -v

# With live RenOS integration
RENOS_SESSION_TOKEN=your-token pytest tests/ -v
```

### 3. Start Server

```bash
# Default (127.0.0.1:8087)
python -m aftergraph_work_intelligence.api

# Custom host/port
python -m aftergraph_work_intelligence.api --host 0.0.0.0 --port 9000

# With authentication
AFTERGRAPH_API_TOKEN=my-secret-token python -m aftergraph_work_intelligence.api
```

---

## Docker Deployment

### 1. Build Image

```bash
docker build -t aftergraph-work-intelligence .
```

### 2. Run Container

```bash
docker run -d \
  -p 8087:8087 \
  -e AFTERGRAPH_API_TOKEN=my-secret-token \
  -e AFTERGRAPH_RATE_LIMIT=120 \
  -v work-intelligence-data:/app/data \
  --name work-intelligence \
  aftergraph-work-intelligence
```

### 3. Docker Compose (Full Stack)

```bash
# Start all services
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f work-intelligence

# Stop all
docker-compose down
```

---

## Production Deployment

### 1. Environment Variables

```bash
# Required
export AFTERGRAPH_API_TOKEN="your-secure-token-here"
export AFTERGRAPH_EVIDENCE_SECRET="your-hmac-secret-here"

# Optional
export AFTERGRAPH_DB="/var/lib/work-intelligence/data.db"
export AFTERGRAPH_HOST="0.0.0.0"
export AFTERGRAPH_PORT="8087"
export AFTERGRAPH_RATE_LIMIT="60"

# RenOS Integration
export RENOS_SESSION_TOKEN="your-renos-session-token"
```

### 2. Systemd Service (Linux)

Create `/etc/systemd/system/work-intelligence.service`:

```ini
[Unit]
Description=Aftergraph Work Intelligence V2
After=network.target

[Service]
Type=simple
User=work-intelligence
Group=work-intelligence
WorkingDirectory=/opt/work-intelligence
ExecStart=/opt/work-intelligence/.venv/bin/python -m aftergraph_work_intelligence.api
Restart=always
RestartSec=5

Environment=AFTERGRAPH_API_TOKEN=your-token
Environment=AFTERGRAPH_DB=/var/lib/work-intelligence/data.db
Environment=AFTERGRAPH_RATE_LIMIT=60

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable work-intelligence
sudo systemctl start work-intelligence
sudo systemctl status work-intelligence
```

### 3. Nginx Reverse Proxy

```nginx
server {
    listen 80;
    server_name work-intelligence.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8087;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Request-ID $request_id;
    }
}
```

### 4. SSL/TLS (Let's Encrypt)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d work-intelligence.yourdomain.com
```

---

## Monitoring

### Health Check

```bash
curl http://localhost:8087/healthz
# {"status": "ok", "service": "aftergraph-work-intelligence", "version": "0.2.0"}
```

### Metrics

```bash
curl -H "Authorization: Bearer $AFTERGRAPH_API_TOKEN" \
  http://localhost:8087/v1/metrics
```

### Logs

```bash
# Docker
docker logs -f work-intelligence

# Systemd
journalctl -u work-intelligence -f
```

---

## Backup and Recovery

### Database Backup

```bash
# SQLite backup
cp /var/lib/work-intelligence/data.db /backup/work-intelligence-$(date +%Y%m%d).db

# Or using sqlite3
sqlite3 /var/lib/work-intelligence/data.db ".backup /backup/work-intelligence-$(date +%Y%m%d).db"
```

### Database Recovery

```bash
# Stop service
sudo systemctl stop work-intelligence

# Restore database
cp /backup/work-intelligence-20260905.db /var/lib/work-intelligence/data.db

# Start service
sudo systemctl start work-intelligence
```

---

## Scaling

### Horizontal Scaling

For multiple instances behind a load balancer:

1. Use shared SQLite (NFS) or migrate to PostgreSQL
2. Configure same `AFTERGRAPH_API_TOKEN` across all instances
3. Use same `AFTERGRAPH_EVIDENCE_SECRET` for consistent HMAC signatures
4. Rate limiting is per-IP, so load balancer should forward `X-Forwarded-For`

### Vertical Scaling

- Increase `AFTERGRAPH_RATE_LIMIT` for higher throughput
- Allocate more memory for SQLite WAL mode
- Use SSD for database storage

---

## Troubleshooting

### Common Issues

**"Rate limit exceeded"**
- Increase `AFTERGRAPH_RATE_LIMIT` or wait 1 minute
- Check for runaway clients

**"Invalid or missing bearer token"**
- Verify `AFTERGRAPH_API_TOKEN` is set
- Check Authorization header format: `Bearer <token>`

**"No publisher destinations configured"**
- Set `AFTERGRAPH_PUBLISHER=renos` or `AFTERGRAPH_PUBLISHER=works`
- Ensure RenOS/WORKS endpoints are accessible

**Database locked**
- Check for concurrent access
- Ensure proper file permissions
- Consider migrating to PostgreSQL for high concurrency

### Debug Mode

```bash
# Enable debug logging
export AFTERGRAPH_LOG_LEVEL=DEBUG
python -m aftergraph_work_intelligence.api
```

---

## Security Considerations

1. **Token Rotation**: Rotate `AFTERGRAPH_API_TOKEN` periodically
2. **HMAC Secret**: Use strong, random secret for `AFTERGRAPH_EVIDENCE_SECRET`
3. **Rate Limiting**: Adjust based on expected traffic
4. **Database Permissions**: Restrict file permissions on SQLite database
5. **Network Security**: Use HTTPS in production (Nginx + Let's Encrypt)
6. **Logging**: Monitor logs for suspicious activity

---

## Support

- GitHub: https://github.com/Aftergraph/work-intelligence-v2
- Issues: https://github.com/Aftergraph/work-intelligence-v2/issues
