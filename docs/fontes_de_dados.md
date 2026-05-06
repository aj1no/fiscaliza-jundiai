# Fontes de Dados - Fiscaliza Jundiai

O sistema coleta dados reais de fontes publicas oficiais de Jundiai/SP. Nenhum coletor deve criar dados simulados.

## 1. Imprensa Oficial de Jundiai

- URL: `https://imprensaoficial.jundiai.sp.gov.br/`
- Conteudo: edicoes da Imprensa Oficial, publicacoes administrativas, editais, atos e comunicados.
- Formato principal: PDF.
- Uso no sistema: documentos oficiais pesquisaveis apos extracao de texto.

## 2. Camara Municipal de Jundiai / SAPL

- Resumos das sessoes: `https://jundiai.sp.leg.br/atividade-legislativa/resumos-das-sessoes`
- SAPL: `https://sapl.jundiai.sp.leg.br/consultas/sessao_plenaria/sessao_plenaria_index_html?iframe=1`
- Conteudo: sessoes plenarias, titulos, datas e links oficiais.
- Formato principal: HTML.
- Uso no sistema: busca textual e futura analise por vereador, tema e linha do tempo.

## 3. Portal da Transparencia

- Portal publico: `https://transparencia.jundiai.sp.gov.br/`
- Sistema de consultas: `https://web21.cijun.sp.gov.br/PMJ/YC/`
- Conteudo atual:
  - licitacoes;
  - contratos;
  - despesas por secretaria;
  - receitas por classificacao orcamentaria.
- Formatos: JSON e CSV.
- Uso no sistema: painel financeiro, busca, analytics de gastos e receitas.

## Politica de coleta

- Priorizar endpoints publicos, CSV, JSON ou XLS quando disponiveis.
- Usar scraping HTML apenas como fallback.
- Usar Playwright somente se `requests` e BeautifulSoup nao forem suficientes.
- Preservar dados anonimizados ou mascarados pela fonte oficial.
- Registrar URL, parametros, status code, hash e resultado da deduplicacao.
- Manter link para a origem oficial em cada documento sempre que possivel.

## Limitacoes

- A disponibilidade dos dados depende dos portais publicos.
- Campos ausentes no endpoint ficam `null`; o sistema nao inventa informacoes.
- Valores agregados por secretaria nao substituem auditoria contabil formal.
- A busca interpretativa e aproximada e sempre deve apontar para documentos oficiais relacionados.
