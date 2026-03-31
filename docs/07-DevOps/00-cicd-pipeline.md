# CI/CD Pipeline Plan for ContextFlow

## Overview

This document outlines a comprehensive CI/CD pipeline following industry best practices for the ContextFlow RAG system. The pipeline supports multiple environments, automated testing, security scanning, and deployment strategies.

## Pipeline Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   Developer     │───▶│   CI Pipeline    │───▶│  CD Pipeline    │
│   (Git Push)    │    │   (GitHub Actions)│    │   (Environments) │
└─────────────────┘    └──────────────────┘    └─────────────────┘
                              │                          │
                              ▼                          ▼
                       ┌──────────────┐         ┌─────────────────┐
                       │   Quality     │         │   Deployment    │
                       │   Gates       │         │   Strategies    │
                       └──────────────┘         └─────────────────┘
```

## Environments

| Environment | Purpose | Trigger | Deployment Strategy |
|-------------|---------|---------|---------------------|
| **Development** | Feature testing | Every push to `feat/*` | Manual/Optional |
| **Staging** | Pre-production validation | Push to `main` | Automated |
| **Production** | Live service | Tagged releases | Manual approval |

## CI Pipeline Stages

### Stage 1: Code Quality & Security

```yaml
# .github/workflows/ci.yml
name: CI Pipeline

on:
  push:
    branches: [main, feat/*]
  pull_request:
    branches: [main]

jobs:
  quality-gates:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
          
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
          
      - name: Lint with Ruff
        run: ruff check src/ tests/
        
      - name: Type check with MyPy
        run: mypy src/
        
      - name: Security scan with Bandit
        run: bandit -r src/
        
      - name: Dependency security scan
        run: safety check
```

### Stage 2: Testing

```yaml
  testing:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ['3.11', '3.12']
        
    services:
      redis:
        image: redis/redis-stack:latest
        ports:
          - 6379:6379
        options: >-
          --health-cmd "redis-cli ping"
          --health-interval 10s
          --health-timeout 5s
          --health-retries 5
          
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        
      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v4
        with:
          python-version: ${{ matrix.python-version }}
          
      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"
          
      - name: Run unit tests
        run: pytest tests/unit/ -v --cov=src --cov-report=xml
        
      - name: Run integration tests
        env:
          REDIS_URL: redis://localhost:6379
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY_TEST }}
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY_TEST }}
        run: pytest tests/integration/ -v
        
      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v3
        with:
          file: ./coverage.xml
```

### Stage 3: Build & Artifact

```yaml
  build:
    runs-on: ubuntu-latest
    needs: [quality-gates, testing]
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        
      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'
          
      - name: Build package
        run: |
          python -m pip install --upgrade pip build
          python -m build
          
      - name: Generate SBOM
        run: |
          pip install cyclonedx-bom
          cyclonedx-py -o sbom.json -i src/
          
      - name: Upload artifacts
        uses: actions/upload-artifact@v3
        with:
          name: dist
          path: dist/
          
      - name: Upload SBOM
        uses: actions/upload-artifact@v3
        with:
          name: sbom
          path: sbom.json
```

## CD Pipeline Stages

### Stage 1: Container Build

```yaml
# .github/workflows/cd.yml
name: CD Pipeline

on:
  push:
    tags: ['v*']
  workflow_run:
    workflows: ["CI Pipeline"]
    types: [completed]
    branches: [main]

jobs:
  container-build:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        
      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3
        
      - name: Login to Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
          
      - name: Build and push
        uses: docker/build-push-action@v5
        with:
          context: .
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            ghcr.io/${{ github.repository }}:latest
            ghcr.io/${{ github.repository }}:${{ github.ref_name }}
          cache-from: type=gha
          cache-to: type=gha,mode=max
          
      - name: Container security scan
        uses: aquasecurity/trivy-action@master
        with:
          image-ref: ghcr.io/${{ github.repository }}:${{ github.ref_name }}
          format: 'sarif'
          output: 'trivy-results.sarif'
          
      - name: Upload Trivy scan results
        uses: github/codeql-action/upload-sarif@v2
        with:
          sarif_file: 'trivy-results.sarif'
```

### Stage 2: Deploy to Staging

```yaml
  deploy-staging:
    runs-on: ubuntu-latest
    needs: container-build
    if: github.ref == 'refs/heads/main'
    environment: staging
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        
      - name: Deploy to staging
        run: |
          echo "Deploying to staging environment"
          # Kubernetes deployment or Docker Compose
          
      - name: Run smoke tests
        run: |
          curl -f http://staging.contextflow.local/health || exit 1
          
      - name: Run integration tests against staging
        env:
          API_BASE_URL: http://staging.contextflow.local
          REDIS_URL: ${{ secrets.STAGING_REDIS_URL }}
          GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}
        run: |
          pytest tests/e2e/ -v --base-url=$API_BASE_URL
```

### Stage 3: Deploy to Production

```yaml
  deploy-production:
    runs-on: ubuntu-latest
    needs: deploy-staging
    if: startsWith(github.ref, 'refs/tags/v')
    environment: production
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        
      - name: Deploy to production
        run: |
          echo "Deploying to production environment"
          # Blue-green deployment or Canary release
          
      - name: Health check
        run: |
          curl -f https://api.contextflow.ai/health || exit 1
          
      - name: Run production smoke tests
        env:
          API_BASE_URL: https://api.contextflow.ai
        run: |
          pytest tests/e2e/production/ -v --base-url=$API_BASE_URL
          
      - name: Notify deployment
        uses: 8398a7/action-slack@v3
        with:
          status: ${{ job.status }}
          channel: '#deployments'
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK }}
```

## Deployment Strategies

### 1. Blue-Green Deployment (Production)

```yaml
# k8s/blue-green-deployment.yml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: contextflow
spec:
  replicas: 3
  strategy:
    blueGreen:
      activeService: contextflow-active
      previewService: contextflow-preview
      autoPromotionEnabled: false
      scaleDownDelaySeconds: 30
      prePromotionAnalysis:
        templates:
        - templateName: success-rate
        args:
        - name: service-name
          value: contextflow-preview
      postPromotionAnalysis:
        templates:
        - templateName: success-rate
        args:
        - name: service-name
          value: contextflow-active
```

### 2. Canary Releases (Alternative)

```yaml
# k8s/canary-deployment.yml
apiVersion: argoproj.io/v1alpha1
kind: Rollout
metadata:
  name: contextflow
spec:
  replicas: 5
  strategy:
    canary:
      steps:
      - setWeight: 20
      - pause: {duration: 10m}
      - setWeight: 40
      - pause: {duration: 10m}
      - setWeight: 60
      - pause: {duration: 10m}
      - setWeight: 80
      - pause: {duration: 10m}
      analysis:
        templates:
        - templateName: success-rate
        - templateName: latency
        args:
        - name: service-name
          value: contextflow
```

## Monitoring & Observability

### 1. Health Checks

```python
# src/contextflow/api/health.py
from fastapi import APIRouter, Depends
from ..redis.client import get_redis_client

router = APIRouter()

@router.get("/health")
async def health_check(redis_client=Depends(get_redis_client)):
    """Comprehensive health check."""
    checks = {
        "redis": await check_redis(redis_client),
        "llm": await check_llm_providers(),
        "memory": check_memory_usage(),
        "disk": check_disk_usage(),
    }
    
    status = "healthy" if all(checks.values()) else "unhealthy"
    return {"status": status, "checks": checks}
```

### 2. Metrics Collection

```python
# src/contextflow/api/metrics.py
from prometheus_client import Counter, Histogram, generate_latest
from fastapi import APIRouter

router = APIRouter()

# Metrics
REQUEST_COUNT = Counter('contextflow_requests_total', 'Total requests', ['method', 'endpoint'])
REQUEST_DURATION = Histogram('contextflow_request_duration_seconds', 'Request duration')
LLM_REQUESTS = Counter('llm_requests_total', 'LLM requests', ['provider', 'model'])

@router.get("/metrics")
async def metrics():
    return generate_latest()
```

### 3. Logging Strategy

```python
# src/contextflow/logging_config.py
import structlog
import logging.config

LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.processors.JSONRenderer(),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
    },
    "loggers": {
        "contextflow": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}
```

## Security & Compliance

### 1. Secret Management

```yaml
# k8s/secrets.yml
apiVersion: v1
kind: Secret
metadata:
  name: contextflow-secrets
type: Opaque
data:
  redis-url: <base64-encoded>
  gemini-api-key: <base64-encoded>
  openai-api-key: <base64-encoded>
```

### 2. Network Policies

```yaml
# k8s/network-policy.yml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: contextflow-netpol
spec:
  podSelector:
    matchLabels:
      app: contextflow
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: ingress-nginx
    ports:
    - protocol: TCP
      port: 8000
  egress:
  - to:
    - namespaceSelector:
        matchLabels:
          name: redis
    ports:
    - protocol: TCP
      port: 6379
```

### 3. Compliance Scanning

```yaml
# .github/workflows/compliance.yml
name: Compliance Scan

on:
  schedule:
    - cron: '0 2 * * *'  # Daily at 2 AM
  workflow_dispatch:

jobs:
  compliance:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        
      - name: Run OWASP ZAP Baseline Scan
        uses: zaproxy/action-baseline@v0
        with:
          target: 'http://staging.contextflow.local'
          
      - name: Run Snyk vulnerability scan
        uses: snyk/actions/python@master
        env:
          SNYK_TOKEN: ${{ secrets.SNYK_TOKEN }}
        with:
          args: --severity-threshold=high
```

## Configuration Management

### 1. Environment-Specific Configs

```python
# src/contextflow/config.py
import os
from enum import Enum
from pydantic import BaseSettings

class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"

class Settings(BaseSettings):
    environment: Environment = Environment.DEVELOPMENT
    
    # Database/Redis
    redis_url: str = "redis://localhost:6379"
    
    # LLM Providers
    gemini_api_key: str = ""
    openai_api_key: str = ""
    
    # Feature flags
    enable_cache: bool = True
    enable_session_memory: bool = True
    
    class Config:
        env_file = f".env.{os.getenv('ENVIRONMENT', 'development')}"
```

### 2. Helm Charts

```yaml
# helm/contextflow/values.yaml
replicaCount: 3

image:
  repository: ghcr.io/vinnybabumanjaly/contextflow
  pullPolicy: IfNotPresent
  tag: "latest"

config:
  environment: production
  redisUrl: ""
  geminiApiKey: ""
  openaiApiKey: ""

resources:
  limits:
    cpu: 500m
    memory: 512Mi
  requests:
    cpu: 250m
    memory: 256Mi

autoscaling:
  enabled: true
  minReplicas: 2
  maxReplicas: 10
  targetCPUUtilizationPercentage: 70
```

## Testing Strategy

### 1. Test Pyramid

```
    ┌─────────────────┐
    │   E2E Tests     │ ← 10% (Critical paths)
    └─────────────────┘
  ┌───────────────────────┐
  │  Integration Tests     │ ← 20% (Component interactions)
  └───────────────────────┘
┌─────────────────────────────┐
│     Unit Tests              │ ← 70% (Fast, isolated)
└─────────────────────────────┘
```

### 2. Test Categories

```python
# tests/conftest.py
import pytest
import asyncio
from contextflow.config import Settings

@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest.fixture
def test_settings():
    """Test settings with mocked external services."""
    return Settings(
        environment="test",
        redis_url="redis://localhost:6379/1",  # Test DB
        gemini_api_key="test-key",
        openai_api_key="test-key",
    )
```

## Performance & Load Testing

### 1. K6 Load Testing

```javascript
// tests/load/k6-test.js
import http from 'k6/http';
import { check, sleep } from 'k6';

export let options = {
  stages: [
    { duration: '2m', target: 10 },   // Ramp up
    { duration: '5m', target: 10 },   // Stay at 10
    { duration: '2m', target: 50 },   // Ramp up to 50
    { duration: '5m', target: 50 },   // Stay at 50
    { duration: '2m', target: 0 },    // Ramp down
  ],
};

export default function () {
  let response = http.post('http://staging.contextflow.local/api/query', {
    query: "How do I optimize Redis performance?",
    stream: false,
  });
  
  check(response, {
    'status is 200': (r) => r.status === 200,
    'response time < 500ms': (r) => r.timings.duration < 500,
  });
  
  sleep(1);
}
```

## Disaster Recovery & Backup

### 1. Database Backup Strategy

```yaml
# k8s/backup-cronjob.yml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: redis-backup
spec:
  schedule: "0 2 * * *"  # Daily at 2 AM
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: redis-backup
            image: redis:latest
            command:
            - /bin/bash
            - -c
            - |
              redis-cli -h $REDIS_HOST -p $REDIS_PORT --rdb /backup/dump-$(date +%Y%m%d).rdb
              aws s3 cp /backup/dump-$(date +%Y%m%d).rdb s3://contextflow-backups/
            env:
            - name: REDIS_HOST
              value: "redis-service"
            - name: REDIS_PORT
              value: "6379"
            - name: AWS_ACCESS_KEY_ID
              valueFrom:
                secretKeyRef:
                  name: aws-credentials
                  key: access-key-id
            - name: AWS_SECRET_ACCESS_KEY
              valueFrom:
                secretKeyRef:
                  name: aws-credentials
                  key: secret-access-key
          restartPolicy: OnFailure
```

## Rollback Strategy

### 1. Automated Rollback

```yaml
# .github/workflows/rollback.yml
name: Emergency Rollback

on:
  workflow_dispatch:
    inputs:
      version:
        description: 'Version to rollback to'
        required: true
        type: string

jobs:
  rollback:
    runs-on: ubuntu-latest
    environment: production
    
    steps:
      - name: Checkout code
        uses: actions/checkout@v4
        
      - name: Rollback deployment
        run: |
          helm rollback contextflow ${{ github.event.inputs.version }} --namespace=production
          
      - name: Verify rollback
        run: |
          curl -f https://api.contextflow.ai/health || exit 1
          
      - name: Notify rollback
        uses: 8398a7/action-slack@v3
        with:
          status: 'rolled_back'
          channel: '#deployments'
          text: "Emergency rollback to version ${{ github.event.inputs.version }} completed"
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK }}
```

## Best Practices Implemented

1. **Infrastructure as Code** - All infrastructure defined in YAML
2. **GitOps** - Deployments managed via Git commits
3. **Security First** - Scanning, secrets management, network policies
4. **Observability** - Metrics, logging, tracing, health checks
5. **Quality Gates** - Automated testing, code quality, security scans
6. **Progressive Delivery** - Blue-green/canary deployments
7. **Disaster Recovery** - Backups, rollback procedures
8. **Compliance** - Regular security scans, vulnerability management

This pipeline provides enterprise-grade CI/CD capabilities while maintaining developer productivity and system reliability.
