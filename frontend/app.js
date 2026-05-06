const API_URL = 'http://localhost:8000';
const DOCUMENT_LIMIT = 500;
const PAGE_SIZE = 10;
const FINANCIAL_YEAR = new Date().getFullYear();

const SOURCES = [
    {
        id: 'imprensa_oficial',
        label: 'Imprensa Oficial',
        description: 'Edições e publicações oficiais do município.',
    },
    {
        id: 'camara_sessoes',
        label: 'Câmara Municipal',
        description: 'Sessões plenárias coletadas do SAPL.',
    },
    {
        id: 'portal_transparencia',
        label: 'Portal da Transparência',
        description: 'Licitações e dados públicos do portal municipal.',
    },
];

const state = {
    documents: [],
    filtered: [],
    pages: {},
    loading: true,
};

const financeState = {
    expenseRows: [],
    expanded: false,
};

function getElement(id) {
    return document.getElementById(id);
}

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function sourceLabel(sourceId) {
    return SOURCES.find((source) => source.id === sourceId)?.label || sourceId || 'Fonte não informada';
}

function formatType(type) {
    if (!type) return 'Tipo não informado';
    return type
        .replaceAll('_', ' ')
        .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatDate(doc) {
    const rawDate = doc.data_publicacao || doc.data_coleta || doc.criado_em;
    if (!rawDate) return 'Sem data';

    const date = new Date(rawDate);
    if (Number.isNaN(date.getTime())) return 'Sem data';
    return date.toLocaleDateString('pt-BR');
}

function formatMoney(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return 'Sem dado';
    }

    return Number(value).toLocaleString('pt-BR', {
        style: 'currency',
        currency: 'BRL',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
    });
}

function formatPercent(value) {
    if (value === null || value === undefined || Number.isNaN(Number(value))) {
        return '--';
    }

    return `${Number(value).toLocaleString('pt-BR', {
        maximumFractionDigits: 1,
        minimumFractionDigits: 1,
    })}%`;
}

function normalizeDocuments(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.value)) return payload.value;
    return [];
}

async function fetchJson(url) {
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Falha na requisição ${url}: ${response.status}`);
    }
    return response.json();
}

async function checkHealth() {
    const statusText = getElement('status-text');
    const indicator = getElement('api-status-indicator');

    try {
        const response = await fetch(`${API_URL}/health`);
        const data = await response.json();
        const online = response.ok && data.status === 'ok';

        statusText.textContent = online ? 'online' : 'com erro';
        indicator.classList.toggle('online', online);
        indicator.classList.toggle('offline', !online);
    } catch (error) {
        statusText.textContent = 'offline';
        indicator.classList.remove('online');
        indicator.classList.add('offline');
    }
}

async function fetchDocumentsBySource(sourceId) {
    const url = `${API_URL}/documents?fonte=${encodeURIComponent(sourceId)}&limit=${DOCUMENT_LIMIT}`;
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`Falha ao buscar ${sourceId}: ${response.status}`);
    }
    return normalizeDocuments(await response.json());
}

async function loadFinancialSummary() {
    getElement('finance-year').textContent = `Ano ${FINANCIAL_YEAR}`;

    try {
        const [revenues, expenses] = await Promise.all([
            fetchJson(`${API_URL}/analytics/receitas?ano=${FINANCIAL_YEAR}&limit=2000`),
            fetchJson(`${API_URL}/analytics/gastos/secretarias?ano=${FINANCIAL_YEAR}`),
        ]);
        renderFinancialSummary(revenues, expenses);
    } catch (error) {
        console.error(error);
        renderFinancialError();
    }
}

function renderFinancialSummary(revenues, expenses) {
    const revenueSummary = calculateRevenueSummary(revenues?.registros || []);
    const expenseRows = Array.isArray(expenses?.secretarias) ? expenses.secretarias : [];
    financeState.expenseRows = expenseRows;
    financeState.expanded = false;

    const totals = calculateExpenseTotals(expenseRows);
    const balance = revenueSummary.collected === null || totals.paid === null
        ? null
        : revenueSummary.collected - totals.paid;
    const paidRatio = revenueSummary.collected && totals.paid !== null
        ? (totals.paid / revenueSummary.collected) * 100
        : null;

    getElement('finance-revenue').textContent = formatMoney(revenueSummary.collected);
    getElement('finance-paid').textContent = formatMoney(totals.paid);
    getElement('finance-balance').textContent = formatMoney(balance);
    getElement('finance-committed').textContent = formatMoney(totals.committed);
    getElement('finance-liquidated').textContent = formatMoney(totals.liquidated);
    getElement('finance-ratio').textContent = formatPercent(paidRatio);

    getElement('finance-revenue-note').textContent = revenueSummary.usesTopLevel
        ? 'Rubricas de topo coletadas'
        : 'Soma dos registros coletados';
    getElement('finance-expense-note').textContent = `${expenseRows.length} secretaria${expenseRows.length === 1 ? '' : 's'} com despesa`;
    getElement('finance-balance-note').textContent = balance === null
        ? 'Aguardando dados oficiais'
        : 'Arrecadado menos gasto pago';
    renderFinanceRanking();
}

function calculateRevenueSummary(rows) {
    const topLevelRows = rows.filter((row) => /^\d0{14}$/.test(String(row.classificacao || '')));
    const usableRows = topLevelRows.length ? topLevelRows : rows;
    const collectedValues = usableRows
        .map((row) => Number(row.valor_arrecadado))
        .filter((value) => Number.isFinite(value));

    return {
        collected: collectedValues.length ? collectedValues.reduce((sum, value) => sum + value, 0) : null,
        usesTopLevel: topLevelRows.length > 0,
    };
}

function calculateExpenseTotals(rows) {
    const sumField = (field) => {
        const values = rows
            .map((row) => Number(row[field]))
            .filter((value) => Number.isFinite(value));
        return values.length ? values.reduce((sum, value) => sum + value, 0) : null;
    };

    return {
        committed: sumField('total_empenhado'),
        liquidated: sumField('total_liquidado'),
        paid: sumField('total_pago'),
    };
}

function renderFinanceRanking() {
    const ranked = [...financeState.expenseRows]
        .filter((row) => Number.isFinite(Number(row.total_pago)) || Number.isFinite(Number(row.total_empenhado)))
        .sort((a, b) => (Number(b.total_pago) || Number(b.total_empenhado) || 0) - (Number(a.total_pago) || Number(a.total_empenhado) || 0));

    const container = getElement('finance-ranking-list');
    if (!ranked.length) {
        container.innerHTML = '<p>Nenhuma secretaria com valor estruturado ainda.</p>';
        return;
    }

    const visibleRows = financeState.expanded ? ranked : ranked.slice(0, 5);
    const toggleButton = ranked.length > 5
        ? `
            <button class="ranking-toggle" data-action="toggle-expenses" type="button">
                ${financeState.expanded ? 'Mostrar top 5' : `Ver todas as ${ranked.length} secretarias`}
            </button>
        `
        : '';
    const summary = financeState.expanded
        ? `Listando todas as ${ranked.length} secretarias com valores estruturados`
        : `Mostrando as 5 maiores de ${ranked.length} secretarias`;

    container.innerHTML = `
        <div class="ranking-summary">
            <span>${escapeHtml(summary)}</span>
            ${toggleButton}
        </div>
        ${visibleRows.map((row, index) => renderFinanceRankingItem(row, index)).join('')}
    `;
}

function renderFinanceRankingItem(row, index) {
    const href = row.url_origem || '#';
    const hasLink = Boolean(row.url_origem);

    return `
        <article class="ranking-item">
            <span class="ranking-position">${index + 1}</span>
            <div class="ranking-content">
                <strong>${escapeHtml(row.secretaria || 'Secretaria não informada')}</strong>
                <div class="ranking-values">
                    <span>
                        <small>Pago</small>
                        <b>${escapeHtml(formatMoney(row.total_pago))}</b>
                    </span>
                    <span>
                        <small>Liquidado</small>
                        <b>${escapeHtml(formatMoney(row.total_liquidado))}</b>
                    </span>
                    <span>
                        <small>Empenhado</small>
                        <b>${escapeHtml(formatMoney(row.total_empenhado))}</b>
                    </span>
                </div>
            </div>
            <a class="${hasLink ? '' : 'disabled'}" href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">Origem</a>
        </article>
    `;
}

function renderFinancialError() {
    getElement('finance-revenue').textContent = 'Sem dado';
    getElement('finance-paid').textContent = 'Sem dado';
    getElement('finance-balance').textContent = 'Sem dado';
    getElement('finance-revenue-note').textContent = 'Não foi possível carregar';
    getElement('finance-expense-note').textContent = 'Não foi possível carregar';
    getElement('finance-balance-note').textContent = 'Verifique a API';
    getElement('finance-ranking-list').innerHTML = '<p>Não foi possível carregar os gastos por secretaria.</p>';
}

async function loadDocuments() {
    state.loading = true;
    renderLoading();

    try {
        const batches = await Promise.all(SOURCES.map((source) => fetchDocumentsBySource(source.id)));
        state.documents = batches.flat();
        state.loading = false;
        populateTypeFilter();
        applyFilters();
    } catch (error) {
        state.loading = false;
        console.error(error);
        getElement('source-sections').innerHTML = '<div class="message error">Não foi possível carregar os documentos.</div>';
        getElement('result-count').textContent = 'Falha ao carregar';
    }
}

function populateTypeFilter() {
    const select = getElement('filter-type');
    const currentValue = select.value;
    const types = [...new Set(state.documents.map((doc) => doc.tipo_documento).filter(Boolean))]
        .sort((a, b) => formatType(a).localeCompare(formatType(b), 'pt-BR'));

    select.innerHTML = '<option value="">Todos</option>';
    types.forEach((type) => {
        const option = document.createElement('option');
        option.value = type;
        option.textContent = formatType(type);
        select.appendChild(option);
    });
    select.value = types.includes(currentValue) ? currentValue : '';
}

function updateCounters(documents) {
    getElement('total-docs').textContent = documents.length;

    SOURCES.forEach((source) => {
        const total = documents.filter((doc) => doc.fonte === source.id).length;
        getElement(`count-${source.id}`).textContent = total;
    });
}

function applyFilters() {
    const query = getElement('main-search').value.trim().toLowerCase();
    const source = getElement('filter-source').value;
    const type = getElement('filter-type').value;

    state.filtered = state.documents.filter((doc) => {
        const matchesSource = !source || doc.fonte === source;
        const matchesType = !type || doc.tipo_documento === type;
        const haystack = [
            doc.titulo,
            doc.fonte,
            sourceLabel(doc.fonte),
            doc.tipo_documento,
            formatType(doc.tipo_documento),
            doc.status_processamento,
            doc.url_origem,
        ].join(' ').toLowerCase();
        const matchesQuery = !query || haystack.includes(query);
        return matchesSource && matchesType && matchesQuery;
    });

    resetPages();
    updateCounters(state.filtered);
    renderDocuments();
}

function resetPages() {
    state.pages = SOURCES.reduce((pages, source) => {
        pages[source.id] = 1;
        return pages;
    }, {});
}

function renderLoading() {
    getElement('source-sections').innerHTML = '<div class="loading">Carregando documentos...</div>';
    getElement('result-count').textContent = 'Carregando...';
}

function renderDocuments() {
    const container = getElement('source-sections');
    const count = state.filtered.length;
    getElement('result-count').textContent = `${count} documento${count === 1 ? '' : 's'} encontrado${count === 1 ? '' : 's'}`;

    if (count === 0) {
        container.innerHTML = '<div class="message">Nenhum documento encontrado com os filtros atuais.</div>';
        return;
    }

    container.innerHTML = SOURCES.map((source) => renderSourceSection(source)).join('');
}

function renderSourceSection(source) {
    const docs = state.filtered.filter((doc) => doc.fonte === source.id);
    const totalPages = Math.max(1, Math.ceil(docs.length / PAGE_SIZE));
    const currentPage = Math.min(state.pages[source.id] || 1, totalPages);
    state.pages[source.id] = currentPage;

    const pageStart = (currentPage - 1) * PAGE_SIZE;
    const visibleDocs = docs.slice(pageStart, pageStart + PAGE_SIZE);
    const body = docs.length
        ? visibleDocs.map(renderDocumentCard).join('')
        : '<div class="empty-source">Nenhum documento para esta fonte nos filtros atuais.</div>';

    return `
        <section class="source-section" aria-labelledby="title-${source.id}">
            <div class="source-header">
                <div>
                    <p class="eyebrow">${escapeHtml(source.description)}</p>
                    <h3 id="title-${source.id}">${escapeHtml(source.label)}</h3>
                </div>
                <span class="source-count">${docs.length}</span>
            </div>
            <div class="document-list">${body}</div>
            ${renderPagination(source.id, docs.length, currentPage, totalPages)}
        </section>
    `;
}

function renderPagination(sourceId, totalItems, currentPage, totalPages) {
    if (totalItems <= PAGE_SIZE) return '';

    const pages = paginationRange(currentPage, totalPages)
        .map((page) => {
            if (page === '...') {
                return '<span class="pagination-gap">...</span>';
            }

            const activeClass = page === currentPage ? 'active' : '';
            return `
                <button class="pagination-btn ${activeClass}" data-source="${sourceId}" data-page="${page}" type="button">
                    ${page}
                </button>
            `;
        })
        .join('');

    const firstItem = (currentPage - 1) * PAGE_SIZE + 1;
    const lastItem = Math.min(currentPage * PAGE_SIZE, totalItems);

    return `
        <div class="pagination-row">
            <p>Mostrando ${firstItem}-${lastItem} de ${totalItems}</p>
            <div class="pagination-controls" aria-label="Paginação">
                <button class="pagination-btn" data-source="${sourceId}" data-page="${currentPage - 1}" type="button" ${currentPage === 1 ? 'disabled' : ''}>
                    Anterior
                </button>
                ${pages}
                <button class="pagination-btn" data-source="${sourceId}" data-page="${currentPage + 1}" type="button" ${currentPage === totalPages ? 'disabled' : ''}>
                    Próxima
                </button>
            </div>
        </div>
    `;
}

function paginationRange(currentPage, totalPages) {
    if (totalPages <= 7) {
        return Array.from({ length: totalPages }, (_, index) => index + 1);
    }

    const pages = new Set([1, totalPages, currentPage, currentPage - 1, currentPage + 1]);
    if (currentPage <= 3) {
        pages.add(2);
        pages.add(3);
        pages.add(4);
    }
    if (currentPage >= totalPages - 2) {
        pages.add(totalPages - 1);
        pages.add(totalPages - 2);
        pages.add(totalPages - 3);
    }

    const sorted = [...pages]
        .filter((page) => page >= 1 && page <= totalPages)
        .sort((a, b) => a - b);

    return sorted.flatMap((page, index) => {
        if (index === 0) return [page];
        return page - sorted[index - 1] > 1 ? ['...', page] : [page];
    });
}

function renderDocumentCard(doc) {
    const title = doc.titulo || 'Documento sem título';
    const originalUrl = doc.url_origem || '#';
    const hasLink = Boolean(doc.url_origem);

    return `
        <article class="document-card">
            <div class="document-main">
                <div class="document-tags">
                    <span>${escapeHtml(sourceLabel(doc.fonte))}</span>
                    <span>${escapeHtml(formatType(doc.tipo_documento))}</span>
                </div>
                <h4>${escapeHtml(title)}</h4>
                <dl class="document-meta">
                    <div>
                        <dt>Data</dt>
                        <dd>${escapeHtml(formatDate(doc))}</dd>
                    </div>
                    <div>
                        <dt>Status</dt>
                        <dd>${escapeHtml(doc.status_processamento || 'não informado')}</dd>
                    </div>
                </dl>
            </div>
            <a class="origin-link ${hasLink ? '' : 'disabled'}" href="${escapeHtml(originalUrl)}" target="_blank" rel="noopener noreferrer">
                Origem
            </a>
        </article>
    `;
}

async function triggerCollect() {
    const button = getElement('btn-collect');
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = 'Enviando...';

    try {
        const response = await fetch(`${API_URL}/collect/manual`, { method: 'POST' });
        if (!response.ok) throw new Error(`Status ${response.status}`);
        button.textContent = 'Coleta enviada';
        setTimeout(() => {
            loadFinancialSummary();
            loadDocuments();
        }, 3000);
    } catch (error) {
        console.error(error);
        button.textContent = 'Falha ao enviar';
    } finally {
        setTimeout(() => {
            button.disabled = false;
            button.textContent = originalText;
        }, 1800);
    }
}

function bindEvents() {
    getElement('main-search').addEventListener('input', applyFilters);
    getElement('filter-source').addEventListener('change', applyFilters);
    getElement('filter-type').addEventListener('change', applyFilters);
    getElement('clear-filters').addEventListener('click', () => {
        getElement('main-search').value = '';
        getElement('filter-source').value = '';
        getElement('filter-type').value = '';
        applyFilters();
    });
    getElement('btn-collect').addEventListener('click', triggerCollect);
    getElement('finance-ranking-list').addEventListener('click', (event) => {
        const button = event.target.closest('[data-action="toggle-expenses"]');
        if (!button) return;

        financeState.expanded = !financeState.expanded;
        renderFinanceRanking();
    });
    getElement('source-sections').addEventListener('click', (event) => {
        const button = event.target.closest('.pagination-btn');
        if (!button || button.disabled) return;

        const sourceId = button.dataset.source;
        const page = Number(button.dataset.page);
        if (!sourceId || !Number.isFinite(page)) return;

        const docs = state.filtered.filter((doc) => doc.fonte === sourceId);
        const totalPages = Math.max(1, Math.ceil(docs.length / PAGE_SIZE));
        state.pages[sourceId] = Math.min(Math.max(page, 1), totalPages);
        renderDocuments();

        document.getElementById(`title-${sourceId}`)?.scrollIntoView({
            behavior: 'smooth',
            block: 'start',
        });
    });
}

document.addEventListener('DOMContentLoaded', () => {
    bindEvents();
    checkHealth();
    loadFinancialSummary();
    loadDocuments();
    setInterval(checkHealth, 30000);
});
