from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# Configuración de la URL de conexión. 
# En desarrollo usaremos variables de entorno o valores por defecto.
# Formato: mysql+pymysql://usuario:password@host:puerto/nombre_bd
#DB_USER = os.getenv("DB_USER", "root")
#DB_PASSWORD = os.getenv("DB_PASSWORD", "tu_password_aqui")
#DB_HOST = os.getenv("DB_HOST", "localhost")
#DB_NAME = os.getenv("DB_NAME", "pucp_cloud_db")

#SQLALCHEMY_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:3306/{DB_NAME}"



# Creamos el motor de conexión
#engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=False)

# --- USAMOS SQLITE PARA PRUEBAS LOCALES ---
SQLALCHEMY_DATABASE_URL = "sqlite:///./test_cloud.db"

# SQLite necesita este argumento extra "check_same_thread" en FastAPI
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})

# Creamos la fábrica de sesiones
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Clase base para nuestros modelos ORM
Base = declarative_base()

# Dependencia para inyectar la sesión de base de datos en los endpoints de FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()