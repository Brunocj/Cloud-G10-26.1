from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os

# Leemos las variables de entorno que inyecta docker-compose
# Si por algún motivo lo corres por fuera, usará los valores por defecto
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "root")
DB_HOST = os.getenv("DB_HOST", "mysql-db") # <-- ¡El nombre del contenedor MySQL!
DB_NAME = os.getenv("DB_NAME", "cloud")

SQLALCHEMY_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:3306/{DB_NAME}"

# Creamos el motor de conexión (ya no necesitamos connect_args de sqlite)
engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=False)

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