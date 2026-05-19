const API_URL = window.API_URL || `${window.location.protocol}//${window.location.hostname}:8000`;
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
    activeTab: 'prefeitura',
};

const financeState = {
    expenseRows: [],
    expanded: false,
    charts: {
        expenses: null,
        comparison: null,
    },
};

const camaraFinanceState = {
    actionRows: [],
    expanded: false,
    charts: {
        expenses: null,
    },
};

function getElement(id) {
    return document.getElementById(id);
}

function moveSummaryToDocumentsTab() {
    const summaryStrip = getElement('documents-summary');
    const documentsTab = getElement('tab-documentos');
    if (!summaryStrip || !documentsTab) return;

    const filtersBar = documentsTab.querySelector('.filters-bar');
    if (summaryStrip.parentElement !== documentsTab) {
        if (filtersBar) {
            documentsTab.insertBefore(summaryStrip, filtersBar);
        } else {
            documentsTab.prepend(summaryStrip);
        }
    }
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

function formatDateTime(value) {
    if (!value) return 'não informada';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return 'não informada';
    return date.toLocaleString('pt-BR');
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

function numberOrNull(value) {
    if (value === null || value === undefined || value === '') return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
}

function reliabilityRank(level) {
    const ranks = {
        consolidado: 0,
        parcial: 1,
        inseguro_para_soma: 2,
    };
    return ranks[level] ?? 1;
}

function worstReliabilityLevel(levels) {
    const valid = levels.filter(Boolean);
    if (!valid.length) return 'parcial';
    return valid.reduce((worst, current) => (
        reliabilityRank(current) > reliabilityRank(worst) ? current : worst
    ), valid[0]);
}

function formatReliabilityLabel(level) {
    if (level === 'consolidado') return 'Consolidado';
    if (level === 'inseguro_para_soma') return 'Inseguro para soma';
    return 'Parcial';
}

function renderQualityWarning(elementId, metadadosList) {
    const warning = getElement(elementId);
    if (!warning) return;
    const items = (Array.isArray(metadadosList) ? metadadosList : [metadadosList])
        .filter(Boolean);
    if (!items.length) {
        warning.hidden = true;
        warning.innerHTML = '';
        return;
    }

    const level = worstReliabilityLevel(items.map((item) => item.nivel_confiabilidade));
    const shouldWarn = level !== 'consolidado' || items.some((item) => item.coleta_completa === false);
    if (!shouldWarn) {
        warning.hidden = true;
        warning.innerHTML = '';
        return;
    }

    const observations = [];
    const collectedSummaries = [];
    const lastCollectDates = [];
    items.forEach((item) => {
        (item.observacoes || []).forEach((note) => {
            const text = String(note || '').trim();
            if (text) observations.push(text);
        });
        if (item.limite_aplicado !== null && item.limite_aplicado !== undefined) {
            observations.push(`Limite aplicado na coleta: ${item.limite_aplicado}`);
        }
        const encontrados = Number.isFinite(Number(item.registros_encontrados))
            ? Number(item.registros_encontrados)
            : null;
        const coletados = Number.isFinite(Number(item.registros_coletados))
            ? Number(item.registros_coletados)
            : null;
        const novos = Number.isFinite(Number(item.registros_novos))
            ? Number(item.registros_novos)
            : null;
        const atualizados = Number.isFinite(Number(item.registros_atualizados))
            ? Number(item.registros_atualizados)
            : null;
        if (encontrados !== null || coletados !== null) {
            let msg = `Registros processados: ${coletados ?? 0} de ${encontrados ?? 0}.`;
            if (novos !== null || atualizados !== null) {
                msg += ` Salvos: ${novos ?? 0}, Atualizados: ${atualizados ?? 0}.`;
            }
            collectedSummaries.push(msg);
        }
        lastCollectDates.push(`Ultima coleta: ${formatDateTime(item.data_ultima_coleta)}.`);
    });
    const uniqueObservations = [...new Set(observations)];
    const uniqueCollected = [...new Set(collectedSummaries)];
    const uniqueDates = [...new Set(lastCollectDates)];
    const summary = level === 'inseguro_para_soma'
        ? 'Atencao: dados inseguros para soma'
        : 'Atencao: dados parciais';
    const detail = uniqueObservations[0]
        || uniqueCollected[0]
        || 'Os valores exibidos representam apenas os dados oficiais coletados ate o momento.';
    const dateLine = uniqueDates[0] || 'Ultima coleta: nao informada.';
    warning.hidden = false;
    warning.className = `quality-warning quality-${level}`;
    warning.innerHTML = `
        <strong>${escapeHtml(summary)}</strong>
        <p>${escapeHtml(`${dateLine} ${detail}`)}</p>
    `;
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

async function loadCamaraFinancialSummary() {
    getElement('camara-finance-year').textContent = `Ano ${FINANCIAL_YEAR}`;

    try {
        const summary = await fetchJson(`${API_URL}/analytics/camara/financeiro?ano=${FINANCIAL_YEAR}`);
        renderCamaraFinancialSummary(summary);
    } catch (error) {
        console.error(error);
        renderCamaraFinancialError();
    }
}

function renderFinancialHealth(health) {
    renderHealthCard(health, {
        card: 'finance-health-card',
        status: 'finance-health-status',
        note: 'finance-health-note',
    });
}

function renderCamaraFinancialHealth(health) {
    renderHealthCard(health, {
        card: 'camara-finance-health-card',
        status: 'camara-finance-health-status',
        note: 'camara-finance-health-note',
    });
}

function renderFinancialSummary(revenues, expenses) {
    const revenueSummary = calculateRevenueSummary(revenues);
    const revenueMeta = revenues?.metadados || {};
    const expenseMeta = expenses?.metadados || {};
    const reliability = worstReliabilityLevel([
        revenueMeta.nivel_confiabilidade,
        expenseMeta.nivel_confiabilidade,
    ]);
    const expenseRows = Array.isArray(expenses?.secretarias) ? expenses.secretarias : [];
    financeState.expenseRows = expenseRows;
    financeState.expanded = false;

    const totals = calculateExpenseTotals(expenseRows);
    const canCompareBalance = Boolean(
        revenueSummary.podeChamarTotal
        && expenseMeta.nivel_confiabilidade === 'consolidado'
        && revenueMeta.nivel_confiabilidade === 'consolidado'
    );
    const balance = !canCompareBalance || revenueSummary.collected === null || totals.paid === null
        ? null
        : revenueSummary.collected - totals.paid;
    const financialHealth = classifyFinancialHealth(revenueSummary.collected, totals.paid, 'arrecadação coletada', reliability);

    getElement('finance-revenue').textContent = formatMoney(revenueSummary.collected);
    getElement('finance-paid').textContent = formatMoney(totals.paid);
    getElement('finance-balance').textContent = formatMoney(balance);
    getElement('finance-committed').textContent = formatMoney(totals.committed);
    getElement('finance-liquidated').textContent = formatMoney(totals.liquidated);

    getElement('finance-revenue-note').textContent = revenueSummary.note;
    getElement('finance-expense-note').textContent = `${expenseRows.length} secretaria${expenseRows.length === 1 ? '' : 's'} com despesa`;
    getElement('finance-balance-note').textContent = balance === null
        ? 'Sem base suficiente para cálculo'
        : 'Diferença entre arrecadação e gasto pago coletados';
    
    renderFinancialHealth(financialHealth);
    renderQualityWarning('finance-quality-warning', [revenueMeta, expenseMeta]);
    renderChartsPrefeitura(revenueSummary, totals, expenseRows);
    renderFinanceRanking();
}

function renderCamaraFinancialSummary(summary) {
    const revenue = numberOrNull(summary?.receita?.total_arrecadado);
    const paid = numberOrNull(summary?.despesa?.total_pago);
    const liquidated = numberOrNull(summary?.despesa?.total_liquidado);
    const committed = numberOrNull(summary?.despesa?.total_empenhado);
    const budget = numberOrNull(summary?.despesa?.dotacao);
    const actionRows = Array.isArray(summary?.acoes) ? summary.acoes : [];
    const camaraMeta = summary?.metadados || {};
    const canCompareBalance = camaraMeta.nivel_confiabilidade === 'consolidado';
    const balance = !canCompareBalance || revenue === null || paid === null ? null : revenue - paid;
    const financialHealth = classifyFinancialHealth(
        revenue,
        paid,
        'receita coletada',
        camaraMeta.nivel_confiabilidade || 'parcial',
    );

    camaraFinanceState.actionRows = actionRows;
    camaraFinanceState.expanded = false;

    getElement('camara-finance-revenue').textContent = formatMoney(revenue);
    getElement('camara-finance-paid').textContent = formatMoney(paid);
    getElement('camara-finance-balance').textContent = formatMoney(balance);
    getElement('camara-finance-budget').textContent = formatMoney(budget);
    getElement('camara-finance-committed').textContent = formatMoney(committed);
    getElement('camara-finance-liquidated').textContent = formatMoney(liquidated);

    getElement('camara-finance-revenue-note').textContent = summary?.receita?.descricao || 'Receitas oficiais coletadas';
    getElement('camara-finance-expense-note').textContent = actionRows.length === 1
        ? '1 ação orçamentária'
        : `${actionRows.length} ações orçamentárias`;
    getElement('camara-finance-balance-note').textContent = balance === null
        ? 'Sem base suficiente para cálculo'
        : 'Diferença entre receita e gasto pago coletados';

    renderCamaraFinancialHealth(financialHealth);
    renderQualityWarning('camara-finance-quality-warning', camaraMeta);
    renderChartsCamara(actionRows, summary);
    renderCamaraFinanceRanking();
}

function calculateRevenueSummary(payload) {
    const meta = payload?.metadados || {};
    const agregacao = payload?.agregacao_receita || {};
    const totalArrecadado = numberOrNull(payload?.total_arrecadado);
    const valorColetado = numberOrNull(payload?.valor_arrecadado_coletado ?? payload?.total_arrecadado);
    const podeChamarTotal = Boolean(
        agregacao?.soma_segura
        && meta?.nivel_confiabilidade === 'consolidado'
        && meta?.coleta_completa === true
    );

    if (podeChamarTotal && totalArrecadado !== null) {
        return {
            collected: totalArrecadado,
            podeChamarTotal: true,
            note: 'Total consolidado com completude confirmada.',
        };
    }

    if (valorColetado !== null) {
        return {
            collected: valorColetado,
            podeChamarTotal: false,
            note: 'Indicador parcial com base em dados oficiais coletados.',
        };
    }

    return {
        collected: null,
        podeChamarTotal: false,
        note: 'Sem base segura para total de arrecadação.',
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

function classifyFinancialHealth(collected, paid, baseLabel = 'arrecadacao coletada', reliabilityLevel = 'parcial') {
    if (!Number.isFinite(Number(collected)) || !Number.isFinite(Number(paid)) || Number(collected) <= 0) {
        return {
            level: 'unknown',
            label: 'Sem leitura',
            note: 'Sem base suficiente para indicador.',
        };
    }

    const ratio = (Number(paid) / Number(collected)) * 100;
    const ratioText = formatPercent(ratio);

    if (reliabilityLevel !== 'consolidado') {
        return {
            level: 'partial',
            label: `Razao: ${ratioText}`,
            note: `Leitura parcial. Pago representa ${ratioText} da ${baseLabel}.`,
        };
    }

    if (ratio <= 100) {
        return {
            level: 'technical',
            label: `Razao: ${ratioText}`,
            note: `Indicador tecnico: pago dividido por ${baseLabel}.`,
        };
    }

    return {
        level: 'warning',
        label: `Razao: ${ratioText}`,
        note: `Indicador tecnico acima de 100%: pago supera ${baseLabel}.`,
    };
}

function renderHealthCard(health, ids) {
    const card = getElement(ids.card);
    card.classList.remove(
        'finance-health-unknown',
        'finance-health-technical',
        'finance-health-warning',
        'finance-health-partial',
    );
    card.classList.add(`finance-health-${health.level}`);
    getElement(ids.status).textContent = health.label;
    getElement(ids.note).textContent = health.note;
}

function renderChartsPrefeitura(revenueSummary, expenseTotals, expenseRows) {
    // 1. Chart Expenses by Secretariat (Doughnut)
    const ctxExp = getElement('chart-prefeitura-expenses')?.getContext('2d');
    if (ctxExp) {
        if (financeState.charts.expenses) financeState.charts.expenses.destroy();

        const topSecretariats = [...expenseRows]
            .sort((a, b) => (b.total_pago || 0) - (a.total_pago || 0))
            .slice(0, 8);

        financeState.charts.expenses = new Chart(ctxExp, {
            type: 'doughnut',
            data: {
                labels: topSecretariats.map((s) => s.secretaria),
                datasets: [{
                    data: topSecretariats.map((s) => s.total_pago),
                    backgroundColor: [
                        '#4da3ff', '#44d7a8', '#ffc45d', '#ff7770',
                        '#b2cae4', '#7cc2ff', '#1b2c3f', '#9fb0c2',
                    ],
                    borderWidth: 0,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'right',
                        labels: { color: '#9fb0c2', font: { size: 10 } },
                    },
                },
            },
        });
    }

    // 2. Chart Revenue vs Expense (Bar)
    const ctxComp = getElement('chart-prefeitura-comparison')?.getContext('2d');
    if (ctxComp) {
        if (financeState.charts.comparison) financeState.charts.comparison.destroy();

        financeState.charts.comparison = new Chart(ctxComp, {
            type: 'bar',
            data: {
                labels: ['Arrecadação', 'Gasto Pago'],
                datasets: [{
                    label: 'Valores em R$',
                    data: [revenueSummary.collected || 0, expenseTotals.paid || 0],
                    backgroundColor: ['#44d7a8', '#ff7770'],
                    borderRadius: 6,
                }],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { display: false } },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { color: '#9fb0c2', font: { size: 10 } },
                        grid: { color: 'rgba(255,255,255,0.05)' },
                    },
                    x: {
                        ticks: { color: '#9fb0c2' },
                        grid: { display: false },
                    },
                },
            },
        });
    }
}

function renderChartsCamara(actionRows, summary) {
    const ctxExp = getElement('chart-camara-expenses')?.getContext('2d');
    if (!ctxExp) return;

    if (camaraFinanceState.charts.expenses) camaraFinanceState.charts.expenses.destroy();

    const topActions = [...actionRows]
        .sort((a, b) => (b.total_pago || 0) - (a.total_pago || 0))
        .slice(0, 8);

    camaraFinanceState.charts.expenses = new Chart(ctxExp, {
        type: 'bar',
        data: {
            labels: topActions.map((a) => a.descricao.slice(0, 20) + '...'),
            datasets: [{
                label: 'Gasto Pago',
                data: topActions.map((a) => a.total_pago),
                backgroundColor: '#7cc2ff',
                borderRadius: 6,
            }],
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
                x: {
                    beginAtZero: true,
                    ticks: { color: '#9fb0c2', font: { size: 10 } },
                    grid: { color: 'rgba(255,255,255,0.05)' },
                },
                y: {
                    ticks: { color: '#9fb0c2', font: { size: 10 } },
                    grid: { display: false },
                },
            },
        },
    });
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
    const title = row.secretaria || row.descricao || 'Item não informado';

    return `
        <article class="ranking-item">
            <span class="ranking-position">${index + 1}</span>
            <div class="ranking-content">
                <strong>${escapeHtml(title)}</strong>
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

function renderCamaraFinanceRanking() {
    const ranked = [...camaraFinanceState.actionRows]
        .filter((row) => Number.isFinite(Number(row.total_pago)) || Number.isFinite(Number(row.total_empenhado)))
        .sort((a, b) => (Number(b.total_pago) || Number(b.total_empenhado) || 0) - (Number(a.total_pago) || Number(a.total_empenhado) || 0));

    const container = getElement('camara-finance-ranking-list');
    if (!ranked.length) {
        container.innerHTML = '<p>Nenhuma ação com valor estruturado ainda.</p>';
        return;
    }

    const visibleRows = camaraFinanceState.expanded ? ranked : ranked.slice(0, 5);
    const toggleButton = ranked.length > 5
        ? `
            <button class="ranking-toggle" data-action="toggle-camara-expenses" type="button">
                ${camaraFinanceState.expanded ? 'Mostrar top 5' : `Ver todas as ${ranked.length} ações`}
            </button>
        `
        : '';
    const summary = camaraFinanceState.expanded
        ? `Listando todas as ${ranked.length} ações com valores estruturados`
        : `Mostrando as 5 maiores de ${ranked.length} ações`;

    container.innerHTML = `
        <div class="ranking-summary">
            <span>${escapeHtml(summary)}</span>
            ${toggleButton}
        </div>
        ${visibleRows.map((row, index) => renderFinanceRankingItem(row, index)).join('')}
    `;
}

function renderFinancialError() {
    getElement('finance-revenue').textContent = 'Sem dado';
    getElement('finance-paid').textContent = 'Sem dado';
    getElement('finance-balance').textContent = 'Sem dado';
    renderFinancialHealth({
        level: 'unknown',
        label: 'Sem dado',
        note: 'Não foi possível carregar o alerta financeiro.',
    });
    getElement('finance-revenue-note').textContent = 'Não foi possível carregar';
    getElement('finance-expense-note').textContent = 'Não foi possível carregar';
    getElement('finance-balance-note').textContent = 'Verifique a API';
    renderQualityWarning('finance-quality-warning', []);
    getElement('finance-ranking-list').innerHTML = '<p>Não foi possível carregar os gastos por secretaria.</p>';
}

function renderCamaraFinancialError() {
    getElement('camara-finance-revenue').textContent = 'Sem dado';
    getElement('camara-finance-paid').textContent = 'Sem dado';
    getElement('camara-finance-balance').textContent = 'Sem dado';
    getElement('camara-finance-budget').textContent = 'Sem dado';
    getElement('camara-finance-committed').textContent = 'Sem dado';
    getElement('camara-finance-liquidated').textContent = 'Sem dado';
    renderCamaraFinancialHealth({
        level: 'unknown',
        label: 'Sem dado',
        note: 'Não foi possível carregar o alerta financeiro da Câmara.',
    });
    getElement('camara-finance-revenue-note').textContent = 'Não foi possível carregar';
    getElement('camara-finance-expense-note').textContent = 'Não foi possível carregar';
    getElement('camara-finance-balance-note').textContent = 'Verifique a API';
    renderQualityWarning('camara-finance-quality-warning', []);
    getElement('camara-finance-ranking-list').innerHTML = '<p>Não foi possível carregar os gastos da Câmara.</p>';
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

function clearAsk() {
    const input = getElement('ask-input');
    const result = getElement('ask-result');
    input.value = '';
    result.hidden = true;
    result.innerHTML = '';
    input.focus();
}

async function runAsk(question) {
    const trimmed = question.trim();
    if (!trimmed) return;

    const input = getElement('ask-input');
    const button = document.querySelector('.ask-submit');
    const result = getElement('ask-result');
    input.value = trimmed;
    button.disabled = true;
    button.textContent = 'Pesquisando...';
    result.hidden = false;
    result.innerHTML = `
        <div class="ask-loading">
            <strong>Buscando nos documentos oficiais...</strong>
            <span>${escapeHtml(trimmed)}</span>
        </div>
    `;

    try {
        const payload = await fetchJson(`${API_URL}/ask?q=${encodeURIComponent(trimmed)}`);
        renderAskResult(payload);
    } catch (error) {
        console.error(error);
        result.innerHTML = `
            <div class="message error">
                Não foi possível consultar os dados agora. Tente novamente em instantes.
            </div>
        `;
    } finally {
        button.disabled = false;
        button.textContent = 'Pesquisar';
    }
}

function renderAskResult(payload) {
    const result = getElement('ask-result');
    const answer = payload?.resposta ?? payload;
    const approximate = Boolean(payload?.baseado_em_aproximacao_textual || answer?.baseado_em_aproximacao_textual);

    if (isServidoresAnswer(payload, answer)) {
        result.innerHTML = renderServidoresAnswer(payload, answer, approximate);
        return;
    }

    if (isSpendingAnswer(answer)) {
        result.innerHTML = renderSpendingAnswer(payload, answer, approximate);
        return;
    }

    if (payload?.tipo === 'analytics_vereador') {
        result.innerHTML = renderVereadorAnswer(payload, answer, approximate);
        return;
    }

    if (answer?.resposta || Array.isArray(answer?.evidencias)) {
        result.innerHTML = renderRagAnswer(payload, answer, approximate);
        return;
    }

    if (Array.isArray(answer)) {
        result.innerHTML = renderSimpleListAnswer(payload, answer, approximate);
        return;
    }

    result.innerHTML = `
        <div class="ask-answer">
            <div class="ask-answer-header">
                <span>${escapeHtml(formatAskType(payload?.tipo))}</span>
                ${approximate ? '<b>Relação provável</b>' : ''}
            </div>
            <p>Encontrei dados relacionados, mas eles ainda não têm um resumo próprio para este tipo de pergunta.</p>
        </div>
    `;
}

function isServidoresAnswer(payload, answer) {
    if (!answer || typeof answer !== 'object') return false;
    return payload?.tipo === 'analytics_servidores_remuneracao'
        || 'total_remuneracao_mes' in answer
        || 'total_remuneracao_bruta' in answer
        || 'total_salario_base' in answer;
}

function isSpendingAnswer(answer) {
    return Boolean(answer && typeof answer === 'object' && (
        'total_pago' in answer
        || 'total_liquidado' in answer
        || 'total_empenhado' in answer
        || Array.isArray(answer.registros)
    ));
}

function renderServidoresAnswer(payload, answer, approximate) {
    const totalMes = numberOrNull(answer.total_remuneracao_mes);
    const totalBruta = numberOrNull(answer.total_remuneracao_bruta);
    const totalBase = numberOrNull(answer.total_salario_base);
    const servidores = Number(answer.servidores || 0);
    const secretarias = Array.isArray(answer.secretarias) ? answer.secretarias.slice(0, 5) : [];
    const documents = uniqueDocuments(answer.documentos || []).slice(0, 5);
    const periodo = [
        answer.mes ? `mês ${String(answer.mes).padStart(2, '0')}` : null,
        answer.ano || null,
    ].filter(Boolean).join('/');
    const periodoLabel = periodo ? `Período consultado: ${periodo}` : 'Período consultado: todos os dados coletados';
    const summary = totalMes && totalMes > 0
        ? 'Encontrei total estruturado de gasto com servidores.'
        : (documents.length || secretarias.length)
            ? 'Encontrei dados oficiais de remuneração, mas sem total consolidado para esta pergunta.'
            : 'Não encontrei dados oficiais de remuneração para essa consulta.';

    return `
        <div class="ask-answer">
            <div class="ask-answer-header">
                <span>${escapeHtml(formatAskType(payload?.tipo))}</span>
                ${approximate ? '<b>Relação provável</b>' : ''}
            </div>
            <h3>${escapeHtml(summary)}</h3>
            <div class="ask-total-grid">
                <div>
                    <span>Total remuneração (mês)</span>
                    <strong>${escapeHtml(formatMoney(totalMes))}</strong>
                </div>
                <div>
                    <span>Total remuneração bruta</span>
                    <strong>${escapeHtml(formatMoney(totalBruta))}</strong>
                </div>
                <div>
                    <span>Total salário base</span>
                    <strong>${escapeHtml(formatMoney(totalBase))}</strong>
                </div>
                <div>
                    <span>Registros analisados</span>
                    <strong>${escapeHtml(String(servidores || 0))}</strong>
                </div>
            </div>
            <p class="ask-note">${escapeHtml(periodoLabel)}</p>
            ${secretarias.length ? `
                <div class="ask-section">
                    <span class="ask-section-title">Top secretarias por remuneração</span>
                    <div class="ask-record-list">
                        ${secretarias.map(renderServidorSecretariaRecord).join('')}
                    </div>
                </div>
            ` : ''}
            ${documents.length ? renderAskDocuments(documents) : ''}
            ${answer.observacao ? `<p class="ask-observation">${escapeHtml(answer.observacao)}</p>` : ''}
        </div>
    `;
}

function renderServidorSecretariaRecord(record) {
    const hasLink = Boolean(record.url_origem);
    return `
        <article class="ask-record">
            <strong>${escapeHtml(record.secretaria || 'Secretaria não informada')}</strong>
            <div class="ask-record-meta">
                <span>Servidores: ${escapeHtml(String(record.servidores || 0))}</span>
                <span>Remuneração mês: ${escapeHtml(formatMoney(record.total_remuneracao_mes))}</span>
            </div>
            <div class="ask-money-row">
                <span>Bruta: <b>${escapeHtml(formatMoney(record.total_remuneracao_bruta))}</b></span>
                <span>Salário base: <b>${escapeHtml(formatMoney(record.total_salario_base))}</b></span>
            </div>
            <a class="${hasLink ? '' : 'disabled'}" href="${escapeHtml(record.url_origem || '#')}" target="_blank" rel="noopener noreferrer">
                Ver origem
            </a>
        </article>
    `;
}

function renderSpendingAnswer(payload, answer, approximate) {
    const positiveTotals = [
        ['Pago identificado', answer.total_pago],
        ['Liquidado identificado', answer.total_liquidado],
        ['Empenhado identificado', answer.total_empenhado],
    ].filter(([, value]) => Number.isFinite(Number(value)) && Number(value) > 0);

    const records = uniqueByUrl(answer.registros || []).slice(0, 5);
    const documents = uniqueDocuments(answer.documentos || []).slice(0, 5);
    const summary = positiveTotals.length
        ? 'Encontrei valores estruturados relacionados à pergunta.'
        : records.length || documents.length
            ? 'Encontrei registros oficiais relacionados, mas sem valor pago ou liquidado estruturado para somar com segurança.'
            : 'Não encontrei registros estruturados nos dados coletados para esta pergunta.';

    return `
        <div class="ask-answer">
            <div class="ask-answer-header">
                <span>${escapeHtml(formatAskType(payload?.tipo))}</span>
                ${approximate ? '<b>Relação provável</b>' : ''}
            </div>
            <h3>${escapeHtml(summary)}</h3>
            ${positiveTotals.length ? `
                <div class="ask-total-grid">
                    ${positiveTotals.map(([label, value]) => `
                        <div>
                            <span>${escapeHtml(label)}</span>
                            <strong>${escapeHtml(formatMoney(value))}</strong>
                        </div>
                    `).join('')}
                </div>
            ` : `
                <p class="ask-note">
                    Isso não significa que o gasto foi zero. Significa que, nas fontes coletadas até agora,
                    não apareceu um valor consolidado confiável para essa pergunta.
                </p>
            `}
            ${records.length ? `
                <div class="ask-section">
                    <span class="ask-section-title">Registros relacionados</span>
                    <div class="ask-record-list">
                        ${records.map(renderAskRecord).join('')}
                    </div>
                </div>
            ` : ''}
            ${documents.length ? renderAskDocuments(documents) : ''}
            ${answer.observacao ? `<p class="ask-observation">${escapeHtml(answer.observacao)}</p>` : ''}
        </div>
    `;
}

function renderAskRecord(record) {
    const title = record.objeto || record.fornecedor || record.secretaria || 'Registro relacionado';
    const hasLink = Boolean(record.url_origem);
    return `
        <article class="ask-record">
            <strong>${escapeHtml(title)}</strong>
            <div class="ask-record-meta">
                <span>${escapeHtml(record.secretaria || 'Secretaria não informada')}</span>
                <span>${escapeHtml(record.fornecedor || 'Fornecedor não informado')}</span>
                <span>${escapeHtml(record.ano || 'Ano não informado')}</span>
            </div>
            <div class="ask-money-row">
                <span>Pago: <b>${escapeHtml(formatMoney(record.valor_pago))}</b></span>
                <span>Liquidado: <b>${escapeHtml(formatMoney(record.valor_liquidado))}</b></span>
                <span>Empenhado: <b>${escapeHtml(formatMoney(record.valor_empenhado))}</b></span>
            </div>
            <a class="${hasLink ? '' : 'disabled'}" href="${escapeHtml(record.url_origem || '#')}" target="_blank" rel="noopener noreferrer">
                Ver origem
            </a>
        </article>
    `;
}

function renderRagAnswer(payload, answer, approximate) {
    const evidences = Array.isArray(answer.evidencias) ? answer.evidencias.slice(0, 5) : [];
    return `
        <div class="ask-answer">
            <div class="ask-answer-header">
                <span>${escapeHtml(formatAskType(payload?.tipo || answer?.tipo))}</span>
                ${approximate ? '<b>Relação provável</b>' : ''}
            </div>
            <h3>${escapeHtml(answer.resposta || 'Encontrei documentos relacionados.')}</h3>
            ${evidences.length ? `
                <div class="ask-section">
                    <span class="ask-section-title">Trechos encontrados</span>
                    <div class="ask-record-list">
                        ${evidences.map(renderAskEvidence).join('')}
                    </div>
                </div>
            ` : ''}
            ${answer.observacao ? `<p class="ask-observation">${escapeHtml(answer.observacao)}</p>` : ''}
        </div>
    `;
}

function renderAskEvidence(item) {
    const doc = item.documento || {};
    const hasLink = Boolean(doc.url_origem);
    return `
        <article class="ask-record">
            <strong>${escapeHtml(doc.titulo || 'Documento relacionado')}</strong>
            <p>${escapeHtml(item.trecho || '').slice(0, 360)}</p>
            <div class="ask-record-meta">
                <span>${escapeHtml(sourceLabel(doc.fonte))}</span>
                <span>${escapeHtml(formatType(doc.tipo_documento))}</span>
            </div>
            <a class="${hasLink ? '' : 'disabled'}" href="${escapeHtml(doc.url_origem || '#')}" target="_blank" rel="noopener noreferrer">
                Ver origem
            </a>
        </article>
    `;
}

function renderSimpleListAnswer(payload, rows, approximate) {
    return `
        <div class="ask-answer">
            <div class="ask-answer-header">
                <span>${escapeHtml(formatAskType(payload?.tipo))}</span>
                ${approximate ? '<b>Relação provável</b>' : ''}
            </div>
            <h3>Encontrei ${rows.length} resultado${rows.length === 1 ? '' : 's'} relacionado${rows.length === 1 ? '' : 's'}.</h3>
            <div class="ask-chip-list">
                ${rows.slice(0, 12).map((row) => `
                    <span>${escapeHtml(Object.values(row).filter(Boolean).join(' - '))}</span>
                `).join('')}
            </div>
        </div>
    `;
}

function renderVereadorAnswer(payload, answer, approximate) {
    const vereador = answer.vereador || {};
    const atuacoes = (answer.atuacoes || []).slice(0, 10);
    const temas = (answer.temas || []).slice(0, 5);
    const documentos = (answer.documentos || []).slice(0, 5);

    return `
        <div class="ask-answer">
            <div class="ask-answer-header">
                <span>Atuação Parlamentar</span>
                ${approximate ? '<b>Relação provável</b>' : ''}
            </div>
            <div class="ask-profile-header">
                <div class="ask-profile-info">
                    <h3>${escapeHtml(vereador.nome || 'Vereador')}</h3>
                    <p>${escapeHtml(vereador.partido || 'Partido não informado')}</p>
                </div>
            </div>

            ${answer.resumo_ia ? `
                <div class="ask-ia-summary">
                    <p>${escapeHtml(answer.resumo_ia)}</p>
                </div>
            ` : ''}

            ${temas.length ? `
                <div class="ask-section">
                    <span class="ask-section-title">Temas mais trabalhados</span>
                    <div class="ask-chip-list">
                        ${temas.map((t) => `<span>${escapeHtml(t.tema)} (${t.total})</span>`).join('')}
                    </div>
                </div>
            ` : ''}

            ${atuacoes.length ? `
                <div class="ask-section">
                    <span class="ask-section-title">Projetos e Atuações</span>
                    <div class="ask-record-list">
                        ${atuacoes.map(renderAtuacaoCard).join('')}
                    </div>
                </div>
            ` : ''}

            ${documentos.length ? renderAskDocuments(documentos) : ''}
            ${answer.observacao ? `<p class="ask-observation">${escapeHtml(answer.observacao)}</p>` : ''}
        </div>
    `;
}

function renderAtuacaoCard(atuacao) {
    const hasLink = Boolean(atuacao.url_origem);
    return `
        <article class="ask-record">
            <div class="ask-record-header">
                <span class="badge">${escapeHtml(atuacao.tipo_atuacao || 'Atuação')}</span>
                ${atuacao.data_atuacao ? `<span>${new Date(atuacao.data_atuacao).toLocaleDateString('pt-BR')}</span>` : ''}
            </div>
            <strong>${escapeHtml(atuacao.titulo || 'Atuação parlamentar')}</strong>
            <p>${escapeHtml(atuacao.descricao || '').slice(0, 200)}${atuacao.descricao?.length > 200 ? '...' : ''}</p>
            <div class="ask-record-meta">
                <span>Tema: ${escapeHtml(atuacao.tema || 'Geral')}</span>
                ${atuacao.bairro ? `<span>Bairro: ${escapeHtml(atuacao.bairro)}</span>` : ''}
            </div>
            <a class="${hasLink ? '' : 'disabled'}" href="${escapeHtml(atuacao.url_origem || '#')}" target="_blank" rel="noopener noreferrer">
                Ver documento completo
            </a>
        </article>
    `;
}

function renderAskDocuments(documents) {
    return `
        <div class="ask-section">
            <span class="ask-section-title">Fontes oficiais</span>
            <div class="ask-source-list">
                ${documents.map((doc) => `
                    <a href="${escapeHtml(doc.url_origem || '#')}" target="_blank" rel="noopener noreferrer" class="${doc.url_origem ? '' : 'disabled'}">
                        <strong>${escapeHtml(doc.titulo || 'Documento oficial')}</strong>
                        <span>${escapeHtml(sourceLabel(doc.fonte))} · ${escapeHtml(formatType(doc.tipo_documento))}</span>
                    </a>
                `).join('')}
            </div>
        </div>
    `;
}

function uniqueByUrl(rows) {
    const seen = new Set();
    return rows.filter((row) => {
        const key = row.url_origem || `${row.objeto}|${row.fornecedor}|${row.secretaria}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function uniqueDocuments(documents) {
    const seen = new Set();
    return documents.filter((doc) => {
        const key = doc.url_origem || doc.id || doc.titulo;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
    });
}

function formatAskType(type) {
    const labels = {
        analytics_gastos_termo: 'Gastos por assunto',
        analytics_gastos_secretaria: 'Gastos por secretaria',
        analytics_gastos_secretarias: 'Gastos por secretarias',
        analytics_servidores_remuneracao: 'Gasto com servidores',
        analytics_receitas: 'Arrecadação',
        analytics_vereador: 'Atuação Parlamentar',
        rag_vetorial_local: 'Busca nos documentos',
        rag_textual_fallback: 'Busca textual',
    };
    return labels[type] || 'Resultado';
}

function switchTab(tabId) {
    const panel = document.querySelector(`[data-tab-panel="${tabId}"]`);
    if (!panel) return;

    state.activeTab = tabId;

    document.querySelectorAll('.tab-button').forEach((button) => {
        const isActive = button.dataset.tab === tabId;
        button.classList.toggle('active', isActive);
        button.setAttribute('aria-selected', String(isActive));
    });

    document.querySelectorAll('.tab-panel').forEach((item) => {
        item.classList.toggle('active', item.dataset.tabPanel === tabId);
    });
}

function bindEvents() {
    getElement('ask-form').addEventListener('submit', (event) => {
        event.preventDefault();
        runAsk(getElement('ask-input').value);
    });
    getElement('ask-clear').addEventListener('click', clearAsk);
    getElement('tabs-nav').addEventListener('click', (event) => {
        const button = event.target.closest('.tab-button');
        if (!button) return;

        switchTab(button.dataset.tab);
    });
    getElement('main-search').addEventListener('input', applyFilters);
    getElement('filter-source').addEventListener('change', applyFilters);
    getElement('filter-type').addEventListener('change', applyFilters);
    getElement('clear-filters').addEventListener('click', () => {
        getElement('main-search').value = '';
        getElement('filter-source').value = '';
        getElement('filter-type').value = '';
        applyFilters();
    });
    getElement('finance-ranking-list').addEventListener('click', (event) => {
        const button = event.target.closest('[data-action="toggle-expenses"]');
        if (!button) return;

        financeState.expanded = !financeState.expanded;
        renderFinanceRanking();
    });
    getElement('camara-finance-ranking-list').addEventListener('click', (event) => {
        const button = event.target.closest('[data-action="toggle-camara-expenses"]');
        if (!button) return;

        camaraFinanceState.expanded = !camaraFinanceState.expanded;
        renderCamaraFinanceRanking();
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

function initApp() {
    moveSummaryToDocumentsTab();
    bindEvents();
    switchTab(state.activeTab);
    loadFinancialSummary();
    loadCamaraFinancialSummary();
    loadDocuments();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initApp);
} else {
    initApp();
}
