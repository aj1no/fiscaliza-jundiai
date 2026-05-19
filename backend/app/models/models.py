from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Float, Date, Boolean, Numeric
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime
import enum

Base = declarative_base()

class StatusProcessamento(str, enum.Enum):
    COLETADO = "coletado"
    PROCESSADO = "processado"
    ERRO = "erro"

class DocumentoBruto(Base):
    __tablename__ = "documentos_brutos"
    id = Column(Integer, primary_key=True, index=True)
    fonte = Column(String(100), nullable=False)
    tipo_documento = Column(String(100))
    titulo = Column(String(255))
    # URL pode se repetir em dados financeiros vivos (mesmo endpoint, conteudo atualizado).
    # A deduplicacao/atualizacao deve ser controlada pelo coletor usando hash e chave natural.
    url_origem = Column(String(500), index=True)
    data_publicacao = Column(DateTime, index=True)
    data_coleta = Column(DateTime, default=datetime.utcnow)
    formato = Column(String(20))
    caminho_arquivo = Column(String(500))
    hash_arquivo = Column(String(64), unique=True)
    hash_texto = Column(String(64))
    status_processamento = Column(String(50), default="coletado")
    erro_processamento = Column(Text)
    criado_em = Column(DateTime, default=datetime.utcnow)
    atualizado_em = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class DocumentoProcessado(Base):
    __tablename__ = "documentos_processados"
    id = Column(Integer, primary_key=True, index=True)
    documento_bruto_id = Column(Integer, ForeignKey("documentos_brutos.id"))
    texto_extraido = Column(Text) # Anteriormente texto_bruto
    texto_limpo = Column(Text)
    tema_principal = Column(String(100), index=True)
    confianca = Column(Float) # Anteriormente confianca_classificacao
    status = Column(String(50))
    criado_em = Column(DateTime, default=datetime.utcnow)
    
    documento_bruto = relationship("DocumentoBruto")


class DocumentoChunk(Base):
    __tablename__ = "documento_chunks"
    id = Column(Integer, primary_key=True, index=True)
    documento_processado_id = Column(Integer, ForeignKey("documentos_processados.id"), index=True, nullable=False)
    documento_bruto_id = Column(Integer, ForeignKey("documentos_brutos.id"), index=True, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    texto = Column(Text, nullable=False)
    texto_limpo = Column(Text, nullable=False)
    embedding = Column(Text)
    embedding_model = Column(String(80), default="local-hash-v1")
    hash_chunk = Column(String(64), index=True)
    tamanho = Column(Integer)
    criado_em = Column(DateTime, default=datetime.utcnow)

    documento_processado = relationship("DocumentoProcessado")
    documento_bruto = relationship("DocumentoBruto")


class LogColeta(Base):
    __tablename__ = "logs_coleta"
    id = Column(Integer, primary_key=True, index=True)
    fonte = Column(String(100)) # Anteriormente coletor
    status = Column(String(50))
    mensagem = Column(Text)
    criado_em = Column(DateTime, default=datetime.utcnow) # Anteriormente data_inicio

# Mantendo as outras tabelas se necessário, mas ajustando nomes de timestamps se houver
class Entidade(Base):
    __tablename__ = "entidades"
    id = Column(Integer, primary_key=True, index=True)
    tipo = Column(String(50), index=True)
    nome = Column(String(255), unique=True)
    metadados = Column(Text)

class DocumentoEntidade(Base):
    __tablename__ = "documento_entidades"
    id = Column(Integer, primary_key=True, index=True)
    documento_id = Column(Integer, ForeignKey("documentos_processados.id"))
    entidade_id = Column(Integer, ForeignKey("entidades.id"))
    valor_extraido = Column(String(255))


class EntidadeExtraida(Base):
    __tablename__ = "entidades_extraidas"
    id = Column(Integer, primary_key=True, index=True)
    documento_processado_id = Column(Integer, ForeignKey("documentos_processados.id"), index=True)
    tipo_entidade = Column(String(80), index=True)
    valor = Column(String(255), index=True)
    contexto = Column(Text)
    confianca = Column(Float)
    criado_em = Column(DateTime, default=datetime.utcnow)

    documento_processado = relationship("DocumentoProcessado")


class Vereador(Base):
    __tablename__ = "vereadores"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(255), nullable=False)
    nome_normalizado = Column(String(255), unique=True, index=True, nullable=False)
    partido = Column(String(50))
    ativo = Column(Boolean, default=True)
    criado_em = Column(DateTime, default=datetime.utcnow)


class AtuacaoVereador(Base):
    __tablename__ = "atuacoes_vereadores"
    id = Column(Integer, primary_key=True, index=True)
    vereador_id = Column(Integer, ForeignKey("vereadores.id"), index=True)
    documento_bruto_id = Column(Integer, ForeignKey("documentos_brutos.id"), index=True)
    tipo_atuacao = Column(String(100))
    titulo = Column(String(255))
    descricao = Column(Text)
    data_atuacao = Column(DateTime)
    tema = Column(String(100), index=True)
    bairro = Column(String(120), index=True)
    url_origem = Column(String(500))
    confianca = Column(Float)
    criado_em = Column(DateTime, default=datetime.utcnow)

    vereador = relationship("Vereador")
    documento_bruto = relationship("DocumentoBruto")


class Secretaria(Base):
    __tablename__ = "secretarias"
    id = Column(Integer, primary_key=True, index=True)
    nome = Column(String(255), nullable=False)
    nome_normalizado = Column(String(255), unique=True, index=True, nullable=False)
    criado_em = Column(DateTime, default=datetime.utcnow)


class Despesa(Base):
    __tablename__ = "despesas"
    id = Column(Integer, primary_key=True, index=True)
    fonte_documento_id = Column(Integer, ForeignKey("documentos_brutos.id"), index=True)
    ano = Column(Integer, index=True)
    secretaria = Column(String(255), index=True)
    fornecedor = Column(String(255))
    cnpj = Column(String(32))
    objeto = Column(Text)
    valor_empenhado = Column(Numeric(18, 2))
    valor_liquidado = Column(Numeric(18, 2))
    valor_pago = Column(Numeric(18, 2))
    data_referencia = Column(DateTime)
    url_origem = Column(String(500))
    criado_em = Column(DateTime, default=datetime.utcnow)

    fonte_documento = relationship("DocumentoBruto")


class Receita(Base):
    __tablename__ = "receitas"
    id = Column(Integer, primary_key=True, index=True)
    fonte_documento_id = Column(Integer, ForeignKey("documentos_brutos.id"), index=True)
    ano = Column(Integer, index=True)
    classificacao = Column(String(255), index=True)
    descricao = Column(Text)
    valor_orcado = Column(Numeric(18, 2))
    valor_arrecadado = Column(Numeric(18, 2))
    percentual = Column(Numeric(12, 6))
    data_referencia = Column(DateTime)
    url_origem = Column(String(500))
    criado_em = Column(DateTime, default=datetime.utcnow)

    fonte_documento = relationship("DocumentoBruto")


class ServidorRemuneracao(Base):
    __tablename__ = "servidores_remuneracao"
    id = Column(Integer, primary_key=True, index=True)
    fonte_documento_id = Column(Integer, ForeignKey("documentos_brutos.id"), index=True)
    ano = Column(Integer, index=True)
    mes = Column(Integer, index=True)
    codigo_funcionario = Column(String(50), index=True)
    nome_funcionario = Column(String(255), index=True)
    secretaria = Column(String(255), index=True)
    cargo = Column(String(255), index=True)
    provimento = Column(String(120))
    carga_horaria = Column(String(40))
    data_admissao = Column(DateTime)
    valor_total_venc = Column(Numeric(18, 2))
    valor_total_mes = Column(Numeric(18, 2))
    valor_salario_base = Column(Numeric(18, 2))
    data_atualizacao = Column(DateTime)
    url_origem = Column(String(500))
    criado_em = Column(DateTime, default=datetime.utcnow)

    fonte_documento = relationship("DocumentoBruto")


class ColetaSnapshot(Base):
    __tablename__ = "coleta_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    fonte = Column(String(100), index=True, nullable=False)
    categoria = Column(String(100), index=True, nullable=False)
    tipo_documento = Column(String(100), index=True)
    ano = Column(Integer, index=True)
    endpoint = Column(String(500))
    parametros = Column(Text)
    status_code = Column(Integer)
    coleta_completa = Column(Boolean, default=False)
    registros_encontrados = Column(Integer)
    registros_coletados = Column(Integer)
    registros_novos = Column(Integer, default=0)
    registros_atualizados = Column(Integer, default=0)
    limite_aplicado = Column(Integer)
    total_itens_informado = Column(Integer)
    hash_conteudo = Column(String(64), index=True)
    nivel_confiabilidade = Column(String(40), default="parcial")
    observacoes = Column(Text)
    criado_em = Column(DateTime, default=datetime.utcnow, index=True)
