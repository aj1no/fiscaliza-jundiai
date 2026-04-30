from .database.database import engine
from .models.models import Base

def init_db():
    print("Inicializando banco de dados...")
    Base.metadata.create_all(bind=engine)
    print("Tabelas criadas com sucesso.")

if __name__ == "__main__":
    init_db()
