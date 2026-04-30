const API_URL = 'http://localhost:8000';
const DOCUMENT_LIMIT = 500;
const PAGE_SIZE = 10;

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

function normalizeDocuments(payload) {
    if (Array.isArray(payload)) return payload;
    if (Array.isArray(payload?.value)) return payload.value;
    return [];
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
        setTimeout(loadDocuments, 3000);
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
    loadDocuments();
    setInterval(checkHealth, 30000);
});
