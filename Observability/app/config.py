import os

# Prometheus
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://prometheus:9090")

# Scheduler interval
OC_UPDATE_INTERVAL = int(os.getenv("OC_UPDATE_INTERVAL", "900"))   # 15 min = 1 ciclo

# Sliding window — 15 días a razón de 1 ciclo cada 15 min
# 15 días × 24h × 4 ciclos/h = 1440 muestras
WINDOW_DAYS     = int(os.getenv("WINDOW_DAYS", "15"))
OC_UPDATE_SECS  = OC_UPDATE_INTERVAL
WINDOW_SIZE     = int((WINDOW_DAYS * 24 * 3600) / OC_UPDATE_SECS)  # muestras en la ventana

# OC safety factors (k per dimension)
K_CPU  = float(os.getenv("K_CPU",  "1.0"))   # 84% confidence
K_RAM  = float(os.getenv("K_RAM",  "2.0"))   # 97.7% confidence

# OC bounds
OC_CPU_DEFAULT  = float(os.getenv("OC_CPU_DEFAULT",  "2.0"))
OC_RAM_DEFAULT  = float(os.getenv("OC_RAM_DEFAULT",  "1.54"))
OC_DISK_DEFAULT = 1.0
OC_RAM_MAX      = float(os.getenv("OC_RAM_MAX", "1.6"))
OC_CPU_MAX      = float(os.getenv("OC_CPU_MAX", "4.0"))

# VM activation thresholds
VM_ACTIVATION_HOURS = int(os.getenv("VM_ACTIVATION_HOURS", "36"))

# Dashboard
ENABLE_DASHBOARD = os.getenv("ENABLE_DASHBOARD", "false").lower() == "true"

# Database
DB_HOST     = os.getenv("DB_HOST",     "host.docker.internal")
DB_USER     = os.getenv("DB_USER",     "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "root")
DB_NAME     = os.getenv("DB_NAME",     "cloud")

# Targets config file path
TARGETS_FILE = os.getenv("TARGETS_FILE", "/app/config/targets.yml")
