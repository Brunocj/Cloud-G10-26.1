import asyncio
import httpx
import time

# La URL de tu endpoint de despliegue
URL = "http://127.0.0.1:8000/slices/1/deploy"

# El cuerpo (JSON) que enviaremos en cada petición
PAYLOAD = {
    "availability_zone": "Linux Cluster",
    "ttl_hours": 4,
    "motivo": "Prueba de estrés por concurrencia"
}

async def send_request(client_session, request_id):
    """Envía una única petición y mide cuánto tarda en responder la API"""
    try:
        start_time = time.time()
        response = await client_session.post(URL, json=PAYLOAD)
        elapsed_time = time.time() - start_time
        
        print(f"Petición #{request_id:02d} | Código: {response.status_code} | "
              f"Respuesta de la API tardó: {elapsed_time:.4f}s")
    except Exception as e:
        print(f"Petición #{request_id:02d} | Error: {str(e)}")

async def main():
    print("🚀 Preparando el cañón de peticiones...")
    NUM_REQUESTS = 20
    
    # Abrimos una sesión asíncrona (como abrir un navegador preparado para disparar)
    async with httpx.AsyncClient() as client:
        # Preparamos las 20 tareas
        tasks = [send_request(client, i+1) for i in range(NUM_REQUESTS)]
        
        print(f"🔥 Disparando {NUM_REQUESTS} solicitudes al MISMO TIEMPO...")
        start_total = time.time()
        
        # asyncio.gather ejecuta todas las tareas a la vez
        await asyncio.gather(*tasks)
        
        print(f"\n✅ Todas las peticiones fueron enviadas y respondidas en: "
              f"{time.time() - start_total:.4f} segundos.")

if __name__ == "__main__":
    # Ejecutamos el ciclo asíncrono
    asyncio.run(main())