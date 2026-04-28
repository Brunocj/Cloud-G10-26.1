Network Orchestrator
Microservicio encargado de la configuración de Redes Definidas por Software (SDN) para el sistema de slices. Actúa como el "músculo" de red de la infraestructura: recibe órdenes de conectividad desde el Queue Manager, aísla topologías lógicas, y materializa los enlaces físicos interactuando con los switches virtuales de los workers mediante SSH.

Descripción General
El Network Orchestrator implementa un diseño agnóstico a la topología. En lugar de procesar geometrías complejas (como mallas, estrellas o anillos), consume un contrato simplificado basado en "enlaces lógicos" (links). Por cada enlace solicitado, el módulo se conecta a los servidores físicos subyacentes, engancha las interfaces virtuales creadas previamente por el aprovisionador de cómputo, y aplica políticas estrictas de aislamiento y seguridad.

El módulo se comunica de forma síncrona a través de NATS (Request/Reply) con el orquestador central, procesando las solicitudes de red en paralelo para reducir drásticamente los tiempos de despliegue en entornos multi-nodo.

Responsabilidades
Conexión de Capa 2: Acoplar las interfaces virtuales de las máquinas (TAPs) al switch de integración central (br-int) basado en Open vSwitch (OVS).

Aislamiento de Tráfico (Multitenancy): Asignar identificadores de VLAN únicos por cada enlace lógico para garantizar que los dominios de broadcast de diferentes alumnos o slices nunca colisionen. Soporta nativamente arquitecturas carrier-grade mediante Q-in-Q (VLAN Stacking) modificando el modo del puerto OVS.

Microsegmentación y Seguridad: Aplicar reglas de cortafuegos (Security Groups) directamente sobre las interfaces virtuales utilizando iptables, permitiendo o denegando tráfico específico (TCP/UDP y puertos) según lo definido en el lienzo del usuario.

Destrucción Limpia: Desvincular puertos y eliminar rastros de VLANs en los switches virtuales al momento de destruir un slice, previniendo fugas de recursos o "puertos fantasma".

Paralelismo Inteligente: Agrupar las tareas de red por dirección IP física del worker, abriendo una única sesión SSH por servidor para configurar múltiples máquinas virtuales simultáneamente sin saturar la red de administración.

Límites del Dominio (Lo que NO hace)
Para mantener una arquitectura limpia de microservicios, el Network Orchestrator delega las siguientes responsabilidades:

No crea las interfaces TAP de red en el sistema operativo anfitrión ni lanza los procesos de virtualización (QEMU) — esto es responsabilidad estricta del Compute Provisioner.

No decide el orden de ejecución de los pasos del despliegue ni guarda el estado general de la transacción en JetStream — esto es responsabilidad del Queue Manager.

No genera las direcciones MAC, no asigna las direcciones IP dinámicas y no decide en qué worker físico se despliega cada recurso — esto es responsabilidad del Slice Manager y el VM Placement.


Arquitectura Interna
El Network Orchestrator sigue una arquitectura de diseño orientada a dominios (Domain-Driven Design) simplificada para microservicios. Está construido en Python asíncrono (asyncio) y segmenta estrictamente la lógica de negocio (orquestación) de la infraestructura técnica (comandos SSH).

La estructura de directorios es la siguiente:

Plaintext
NetworkOrchestrator/
 ├── Dockerfile                 → Definición de la imagen del contenedor
 ├── docker-compose.yml         → Orquestación local acoplada a la red del clúster
 ├── main.py                    → Punto de entrada: inicializa NATS y el ciclo de vida
 ├── requirements.txt           → Dependencias estrictas (Pydantic, NATS, Paramiko)
 └── app/
      ├── __init__.py           
      ├── api/
      │    └── health.py        → GET /health (Healthcheck HTTP para monitoreo)
      ├── core/
      │    ├── config.py        → Carga de variables de entorno (Pydantic Settings)
      │    └── logging_config.py→ Formateo estandarizado de logs
      ├── models/
      │    └── schemas.py       → Contratos de datos (Pydantic): define NetworkLink
      └── services/
           ├── handlers.py        → Interceptores de NATS: validan JSON y llaman al Provisioner
           ├── provisioner.py     → Cerebro Lógico: agrupa tareas por worker y ejecuta en paralelo
           ├── network_executor.py→ Músculo Físico: ejecuta comandos crudos OVS/iptables por SSH
           ├── ssh_client.py      → Wrapper de Paramiko con llaves PEM en memoria (Thread-Safe)
           └── queue_client.py    → Cliente NATS (Core NATS Request/Reply)
Descripción de Componentes Clave
models/schemas.py (El Contrato Central): Es el archivo más crítico para la integración. Define que la topología no se recibe como un grafo geométrico, sino como una lista plana de objetos NetworkLink. Garantiza que cada cable tenga un Extremo A, un Extremo B y un identificador VLAN.

services/provisioner.py (Optimizador de Concurrencia): Para evitar cuellos de botella y maximizar el rendimiento, este módulo desarma la topología lógica, agrupa las interfaces (TAPs) que pertenecen a un mismo servidor físico y abre una sola conexión SSH por worker, ejecutando la configuración de múltiples puertos en paralelo utilizando ThreadPoolExecutor.

services/network_executor.py (Agnóstico a la Topología): Contiene la sintaxis específica de Linux y Open vSwitch. Aplica el flag estratégico --may-exist al crear puentes virtuales y es el único archivo que interactúa directamente con ovs-vsctl e iptables.

services/queue_client.py: A diferencia del Queue Manager que utiliza JetStream para persistir eventos, este cliente utiliza la mensajería síncrona Core NATS (Patrón Request/Reply), asegurando que el flujo del orquestador se detenga hasta confirmar que la red física fue exitosamente configurada.


Aquí tienes exclusivamente la Parte 3 redactada con todo el rigor técnico, manteniendo el estándar visual de tu equipo y detallando el contrato de enlaces lógicos que diseñamos.Flujo de Mensajes y ContratosEl Network Orchestrator utiliza Core NATS (Request/Reply) para la comunicación con el Queue Manager. A diferencia de otros componentes, este módulo no utiliza JetStream para persistencia de eventos, ya que su operación es puramente síncrona: recibe una orden, la ejecuta en la infraestructura física y responde el resultado por el mismo canal temporal.Este diseño garantiza que el Queue Manager bloquee el avance del flujo general hasta confirmar que la red física ha sido aprovisionada o destruida exitosamente.Diagrama de FlujoPlaintextQueue Manager
    │
    │  NATS request → network.deploy
    │  { slice_id, request_id, links: [{ connection_id, vlan_id, vm1_..., vm2_... }] }
    ▼
Network Orchestrator
    │
    ├─ Desglosa y agrupa los extremos de los enlaces por IP de Worker físico
    ├─ SSH → workerN: ovs-vsctl --may-exist add-port br-int tap-X
    ├─ SSH → workerN: ovs-vsctl set port tap-X tag=VLAN
    ├─ SSH → workerN: iptables -I FORWARD ... (Aplicación de Security Groups)
    │
    │  NATS reply → Queue Manager
    │  { slice_id, request_id, status, links_ok: [...], links_failed: [...] }
    ▼
Queue Manager
Formato de MensajesEl contrato de entrada es agnóstico a la topología geométrica (bus, estrella, malla). En su lugar, el orquestador recibe una lista plana de links (enlaces de capa 2). Cada enlace representa un "cable virtual" y define una VLAN única junto con las dos interfaces de red (TAPs) que debe conectar.Entrada: network.deployJSON{
  "slice_id": "slice-test-001",
  "request_id": "req-001",
  "links": [
    {
      "connection_id": "link-1-vlan-100",
      "vlan_id": 100,
      "vm1_id": "vm-1",
      "vm1_worker_ip": "10.0.10.2",
      "vm1_tap": "tap-vm1",
      "vm1_ssh_user": "ubuntu",
      "vm1_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm1_security_rules": [
        {"allow_port": 80, "protocol": "tcp"}
      ],
      "vm2_id": "vm-2",
      "vm2_worker_ip": "10.0.10.3",
      "vm2_tap": "tap-vm2",
      "vm2_ssh_user": "ubuntu",
      "vm2_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm2_security_rules": []
    }
  ]
}
Salida: Respuesta a Deploy (Éxito)JSON{
  "slice_id": "slice-test-001",
  "request_id": "req-001",
  "status": "success",
  "links_ok": [
    {
      "connection_id": "link-1-vlan-100",
      "error": null
    }
  ],
  "links_failed": []
}
Entrada: network.destroyJSON{
  "slice_id": "slice-test-001",
  "request_id": "req-002"
}
Salida: Respuesta a DestroyJSON{
  "slice_id": "slice-test-001",
  "request_id": "req-002",
  "status": "success",
  "error": null
}
Valores de statusEl módulo evalúa el resultado global basado en el éxito individual de cada enlace configurado:ValorSignificadosuccessTodos los enlaces lógicos de la topología se configuraron en los switches virtuales correctamente.errorFallo crítico. Ningún enlace pudo ser configurado (ej. error generalizado de conexión SSH).partialAlgunos enlaces se configuraron correctamente, pero otros fallaron. El Queue Manager registrará esto para un posible rollback de la transacción.


Prerrequisitos en la Infraestructura Física
El Network Orchestrator opera bajo el supuesto de que los servidores físicos (workers) ya cuentan con las herramientas de red necesarias instaladas a nivel de sistema operativo. Al conectarse vía SSH, el módulo inyecta comandos directamente en la terminal, por lo que la ausencia de estas dependencias provocará un fallo inmediato en el despliegue del slice.

Cada nodo de cómputo en el clúster debe cumplir estrictamente con los siguientes requisitos:

1. Open vSwitch (OVS) Instalado
OVS es el núcleo de conmutación virtual de la plataforma. Debe estar instalado y habilitado como servicio nativo en el sistema operativo (típicamente Ubuntu/Debian).

Bash
sudo apt update
sudo apt install -y openvswitch-switch
sudo systemctl enable --now openvswitch-switch
2. Puente de Integración Base (br-int)
El sistema utiliza una arquitectura de red estandarizada donde todas las interfaces de las máquinas virtuales (TAPs) convergen en un único "panel de conexiones" virtual. Se asume la existencia de un bridge llamado br-int.

Aunque el orquestador ejecuta el comando con el flag de seguridad --may-exist, se considera una mejor práctica inicializarlo manualmente durante el alta del servidor físico:

Bash
sudo ovs-vsctl --may-exist add-br br-int
3. Soporte de Kernel para Filtrado en Capa 2
Para que el módulo pueda cumplir con el requerimiento de microsegmentación (Security Groups) utilizando iptables sobre las interfaces conectadas al switch virtual, el kernel de Linux del worker debe cargar el módulo br_netfilter y habilitar el paso de tráfico de bridge hacia iptables.

Bash
# Cargar el módulo en el kernel
sudo modprobe br_netfilter

# Asegurar que los paquetes que cruzan el bridge sean procesados por iptables
echo "net.bridge.bridge-nf-call-iptables = 1" | sudo tee -a /etc/sysctl.conf
echo "net.bridge.bridge-nf-call-ip6tables = 1" | sudo tee -a /etc/sysctl.conf
sudo sysctl -p
4. Permisos SSH y Ejecución sin Contraseña (Passwordless Sudo)
Al igual que el Compute Provisioner, el Network Orchestrator requiere que las credenciales provistas en el mensaje network.deploy correspondan a un usuario (ej. ubuntu) que posea privilegios para ejecutar comandos sudo sin que se le solicite contraseña interactivamente.

Para configurar esto en el worker físico, se debe editar el archivo de sudoers (sudo visudo) y añadir la siguiente regla para el usuario:

Plaintext
ubuntu ALL=(ALL) NOPASSWD: /usr/bin/ovs-vsctl, /sbin/iptables, /usr/sbin/iptables
(Nota: Para entornos de desarrollo o laboratorios con red controlada, a menudo se usa ubuntu ALL=(ALL) NOPASSWD: ALL para mayor simplicidad, aunque se recomienda la restricción por binarios en producción).



Aquí tienes la Parte 5, el cierre triunfal de tu documentación. Esta sección es la guía operativa definitiva para que cualquier desarrollador pueda clonar tu repositorio, encender el módulo de red y probarlo en cuestión de segundos.Configuración y DespliegueEl Network Orchestrator está diseñado para ser completamente stateless (sin estado propio) y configurable mediante variables de entorno, lo que facilita su despliegue en contenedores Docker y su integración continua.Variables de EntornoPuedes configurar el servicio creando un archivo .env en la raíz del proyecto. Si no se proveen, el sistema asume los siguientes valores por defecto:VariableValor por DefectoDescripciónNATS_URLnats://nats:4222URL de conexión al broker de mensajería del clúster.LOG_LEVELINFONivel de verbosidad de los registros (DEBUG, INFO, WARNING, ERROR).HEALTH_PORT8084Puerto interno expuesto para el monitoreo de vida del contenedor.MAX_CONCURRENT_WORKERS10Límite máximo de conexiones SSH asíncronas simultáneas hacia distintos servidores físicos.SSH_TIMEOUT30Segundos máximos de espera para que un comando ovs-vsctl o iptables responda.Despliegue con Docker ComposeEl módulo está preparado para integrarse a la red aislada compartida del orquestador central (queuemanager_default). Para desplegarlo:Asegúrate de estar en el directorio raíz del microservicio (donde se encuentra el docker-compose.yml).Construye e inicia el contenedor en segundo plano:Bashsudo docker compose up --build -d
Verifica los logs para confirmar la correcta suscripción a los canales de red:Bashsudo docker compose logs -f
(Deberías ver mensajes confirmando la conexión a NATS y la escucha en network.deploy y network.destroy).Verificación de Estado (Healthcheck)El microservicio expone un servidor HTTP ultraligero que verifica de forma continua su propia conexión al bus de eventos. Puedes monitorearlo localmente con:Bashcurl http://localhost:8084/health
Respuesta Esperada (HTTP 200):JSON{"status": "ok", "nats": true}
(Si se pierde la conexión a NATS, devolverá HTTP 503 con "status": "degraded").Pruebas de Aislamiento (Manuales)Para validar la correcta configuración de llaves SSH y la respuesta de los comandos OVS/iptables sin necesidad de levantar el Slice Manager o el Queue Manager, puedes simular la inyección de un requerimiento directamente en la red de contenedores utilizando nats-box:Bashsudo docker run --network=queuemanager_default --rm -it natsio/nats-box nats req -s nats://nats:4222 network.deploy '{
  "slice_id": "prueba-aislada-01",
  "request_id": "req-999",
  "links": [
    {
      "connection_id": "link-1",
      "vlan_id": 100,
      "vm1_id": "vm-1",
      "vm1_worker_ip": "192.168.1.50",
      "vm1_tap": "tap-vm1",
      "vm1_ssh_user": "ubuntu",
      "vm1_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm1_security_rules": [],
      "vm2_id": "vm-2",
      "vm2_worker_ip": "192.168.1.50",
      "vm2_tap": "tap-vm2",
      "vm2_ssh_user": "ubuntu",
      "vm2_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm2_security_rules": []
    }
  ]
}'
El contenedor interceptará el JSON, intentará ejecutar la configuración en los TAPs del servidor indicado y te devolverá el JSON de respuesta de forma instantánea en la misma consola.