from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional, List

class DocumentoBrutoBase(BaseModel):
    fonte: str
    tipo_documento: Optional[str] = None
    titulo: Optional[str] = None
    url_origem: str
    data_publicacao: Optional[datetime] = None
    formato: Optional[str] = None

class DocumentoBruto(DocumentoBrutoBase):
    id: int
    data_coleta: datetime
    status_processamento: str
    criado_em: datetime
    atualizado_em: datetime

    model_config = ConfigDict(from_attributes=True)

class DocumentoProcessadoBase(BaseModel):
    texto_limpo: Optional[str] = None
    tema_principal: Optional[str] = None
    confianca: Optional[float] = None
    status: Optional[str] = None

class DocumentoProcessado(DocumentoProcessadoBase):
    id: int
    documento_bruto_id: int
    criado_em: datetime

    model_config = ConfigDict(from_attributes=True)

class HealthResponse(BaseModel):
    status: str
    database: str
    redis: str
