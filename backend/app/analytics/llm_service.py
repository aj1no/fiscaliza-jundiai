import os
import logging
import google.generativeai as genai
from typing import List, Optional

logger = logging.getLogger(__name__)

# Configuração da API do Gemini
# O usuário deve adicionar GEMINI_API_KEY no arquivo .env
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
else:
    logger.warning("GEMINI_API_KEY não configurada. A integração com LLM estará desativada.")

def generate_answer(question: str, evidences: List[dict], structured_data: Optional[dict] = None) -> str:
    """
    Gera uma resposta em linguagem natural usando o Gemini com base no contexto fornecido.
    """
    if not GEMINI_API_KEY:
        return "Integração com IA generativa desativada (chave de API ausente)."

    if not evidences and not structured_data:
        return "Não encontrei informações oficiais suficientes para gerar uma análise detalhada."

    # Preparar o contexto a partir das evidências (chunks)
    context_text = ""
    for i, item in enumerate(evidences, 1):
        doc = item.get("documento") or {}
        trecho = item.get("trecho") or ""
        context_text += f"\n--- DOCUMENTO {i} ({doc.get('titulo', 'Sem título')}) ---\n{trecho}\n"

    # Preparar dados estruturados se houver
    structured_text = ""
    if structured_data:
        structured_text = f"\n--- DADOS FINANCEIROS ESTRUTURADOS ---\n{str(structured_data)}\n"

    prompt = f"""
Você é o assistente virtual do projeto "Fiscaliza Jundiaí", uma plataforma de transparência pública.
Sua missão é responder à pergunta do cidadão de forma clara, objetiva e baseada EXCLUSIVAMENTE nos dados oficiais fornecidos abaixo.

DIRETRIZES:
1. Use os "TRECHOS DE DOCUMENTOS" para embasar sua resposta.
2. Se houver "DADOS FINANCEIROS ESTRUTURADOS", use-os como prioridade para valores monetários.
3. Se a informação não estiver nos dados fornecidos, diga educadamente que não encontrou esse detalhe específico nos registros atuais.
4. Mantenha um tom profissional, mas acessível ao cidadão.
5. Não invente dados. Cite nomes de secretarias, valores e datas quando disponíveis.
6. Se a resposta envolver múltiplos documentos, tente sintetizar os pontos principais.

PERGUNTA DO CIDADÃO:
{question}

{structured_text}

TRECHOS DE DOCUMENTOS OFICIAIS (CONTEXTO):
{context_text}

RESPOSTA PARA O CIDADÃO:
"""

    try:
        model = genai.GenerativeModel('gemini-1.5-flash-latest')
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logger.error(f"Erro ao chamar Gemini API: {str(e)}")
        return "Ocorreu um erro ao processar a resposta com IA. Por favor, verifique os trechos oficiais abaixo."
