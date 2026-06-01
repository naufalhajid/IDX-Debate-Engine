document.addEventListener('DOMContentLoaded', () => {
    const header = document.querySelector('.site-header');
    const menu = document.querySelector('#site-menu');
    const menuButton = document.querySelector('.menu-button');
    const navLinks = document.querySelectorAll('.site-nav a[href^="#"]');
    const revealTargets = document.querySelectorAll('.section-heading, .feature-card, .command-card, .flow-grid article, .snapshot-panel, .candidate-table, .signal-grid article');
    const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const artifactViewer = document.querySelector('#artifact-viewer');
    const artifactTitle = document.querySelector('#artifact-title');
    const artifactBody = document.querySelector('#artifact-body');
    const artifactClose = document.querySelector('.artifact-close');

    const artifacts = {
        shortlist: {
            title: 'Candidate shortlist',
            source: 'artifacts/top10_candidates.json',
            type: 'shortlist'
        },
        results: {
            title: 'Debate results',
            source: 'artifacts/full_batch_results.json',
            type: 'results'
        },
        report: {
            title: 'Final report',
            source: 'artifacts/TOP_3_SWING_TRADES.md',
            type: 'report'
        }
    };

    const closeMenu = () => {
        if (!menu || !menuButton) return;
        menu.classList.remove('open');
        document.body.classList.remove('menu-open');
        menuButton.setAttribute('aria-expanded', 'false');
    };

    if (menu && menuButton) {
        menuButton.addEventListener('click', () => {
            const isOpen = menu.classList.toggle('open');
            document.body.classList.toggle('menu-open', isOpen);
            menuButton.setAttribute('aria-expanded', String(isOpen));
        });
    }

    navLinks.forEach((link) => {
        link.addEventListener('click', (event) => {
            const href = link.getAttribute('href');
            const target = href ? document.querySelector(href) : null;
            if (!target) return;

            event.preventDefault();
            closeMenu();

            const offset = header ? header.offsetHeight + 16 : 88;
            window.scrollTo({
                top: target.offsetTop - offset,
                behavior: reduceMotion ? 'auto' : 'smooth'
            });
        });
    });

    document.querySelectorAll('.copy-button').forEach((button) => {
        button.addEventListener('click', async () => {
            const value = button.getAttribute('data-copy') || '';
            try {
                await navigator.clipboard.writeText(value);
                button.textContent = 'Copied';
                button.classList.add('copied');
                window.setTimeout(() => {
                    button.textContent = 'Copy';
                    button.classList.remove('copied');
                }, 1400);
            } catch {
                button.textContent = 'Select text';
            }
        });
    });

    const createElement = (tag, className, text) => {
        const element = document.createElement(tag);
        if (className) element.className = className;
        if (text !== undefined) element.textContent = text;
        return element;
    };

    const formatNumber = (value, digits = 1) => {
        const number = Number(value);
        if (!Number.isFinite(number)) return '-';
        return new Intl.NumberFormat('en-US', {
            maximumFractionDigits: digits,
            minimumFractionDigits: Number.isInteger(number) ? 0 : digits
        }).format(number);
    };

    const formatCurrency = (value) => {
        const number = Number(value);
        if (!Number.isFinite(number)) return '-';
        return `Rp ${new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(number)}`;
    };

    const appendStat = (container, value, label) => {
        const card = createElement('div', 'artifact-stat');
        card.append(createElement('strong', '', value));
        card.append(createElement('span', '', label));
        container.append(card);
    };

    const renderTable = (columns, rows) => {
        const wrap = createElement('div', 'artifact-table-wrap');
        const table = createElement('table', 'artifact-table');
        const thead = document.createElement('thead');
        const headRow = document.createElement('tr');
        columns.forEach((column) => headRow.append(createElement('th', '', column.label)));
        thead.append(headRow);
        table.append(thead);

        const tbody = document.createElement('tbody');
        rows.forEach((row) => {
            const tr = document.createElement('tr');
            columns.forEach((column) => tr.append(createElement('td', '', column.value(row))));
            tbody.append(tr);
        });
        table.append(tbody);
        wrap.append(table);
        return wrap;
    };

    const renderShortlist = (data) => {
        const fragment = document.createDocumentFragment();
        const rows = Array.isArray(data) ? data : [];
        const topScore = rows.reduce((max, row) => Math.max(max, Number(row['Composite Score']) || 0), 0);
        const clearExDate = rows.filter((row) => row['ExDate Risk'] === 'CLEAR').length;

        const stats = createElement('div', 'artifact-summary-grid');
        appendStat(stats, String(rows.length), 'ranked candidates');
        appendStat(stats, formatNumber(topScore), 'top composite score');
        appendStat(stats, String(clearExDate), 'clear ex-date checks');
        fragment.append(stats);

        fragment.append(renderTable([
            { label: 'Ticker', value: (row) => row.Ticker || '-' },
            { label: 'Score', value: (row) => formatNumber(row['Composite Score']) },
            { label: 'Price', value: (row) => formatCurrency(row['Current Price']) },
            { label: 'Valuation gap', value: (row) => `${formatNumber(row['Valuation Gap (%)'])}%` },
            { label: 'F-Score', value: (row) => formatNumber(row['Piotroski F-Score'], 0) },
            { label: 'Setup note', value: (row) => row['Entry Strategy'] || '-' }
        ], rows.slice(0, 8)));

        return fragment;
    };

    const verdictLabel = (rating) => {
        if (rating === 'HOLD') return 'Watchlist';
        if (rating === 'AVOID') return 'Rejected';
        if (rating === 'INSUFFICIENT_DATA') return 'SKIP TICKER';
        return rating || 'Unknown';
    };

    const verdictClass = (rating) => {
        if (rating === 'HOLD') return 'hold';
        if (rating === 'AVOID') return 'avoid';
        return 'skip';
    };

    const renderResults = (data) => {
        const fragment = document.createDocumentFragment();
        const rows = Array.isArray(data) ? data : [];
        const counts = rows.reduce((acc, row) => {
            const rating = row.verdict?.rating || 'UNKNOWN';
            acc[rating] = (acc[rating] || 0) + 1;
            return acc;
        }, {});
        const avgRounds = rows.length
            ? rows.reduce((sum, row) => sum + Number(row.debate_rounds || 0), 0) / rows.length
            : 0;

        const stats = createElement('div', 'artifact-summary-grid');
        appendStat(stats, String(rows.length), 'tickers processed');
        appendStat(stats, `${counts.HOLD || 0}/${counts.AVOID || 0}/${counts.INSUFFICIENT_DATA || 0}`, 'watchlist / rejected / skipped');
        appendStat(stats, formatNumber(avgRounds), 'average debate rounds');
        fragment.append(stats);

        const grid = createElement('div', 'verdict-grid');
        rows.slice(0, 10).forEach((row) => {
            const verdict = row.verdict || {};
            const card = createElement('article', 'verdict-card');
            const top = createElement('div', 'verdict-topline');
            top.append(createElement('span', 'verdict-ticker', row.ticker || verdict.ticker || '-'));
            top.append(createElement('span', `verdict-pill ${verdictClass(verdict.rating)}`, verdictLabel(verdict.rating)));
            card.append(top);

            const meta = createElement('div', 'verdict-meta');
            meta.append(createElement('span', '', `Confidence: ${formatNumber((verdict.confidence || 0) * 100, 0)}%`));
            meta.append(createElement('span', '', `Consensus: ${row.consensus_method || '-'} / ${row.debate_rounds || 0} round(s)`));
            meta.append(createElement('span', '', `Entry: ${verdict.entry_price_range || 'not approved'}`));
            meta.append(createElement('span', '', `Target / Stop: ${verdict.target_price ? formatCurrency(verdict.target_price) : '-'} / ${verdict.stop_loss ? formatCurrency(verdict.stop_loss) : '-'}`));
            card.append(meta);
            grid.append(card);
        });
        fragment.append(grid);

        return fragment;
    };

    const renderReport = (text) => {
        const fragment = document.createDocumentFragment();
        const lines = text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
        const title = lines.find((line) => line.startsWith('# '))?.replace(/^#\s*/, '') || 'Final report';
        const metaLines = lines.filter((line) => line.startsWith('>')).map((line) => line.replace(/^>\s*/, ''));
        const bodyLines = lines.filter((line) => !line.startsWith('#') && !line.startsWith('>') && line !== '---');

        const summary = createElement('article', 'report-card');
        summary.append(createElement('h3', '', title));
        summary.append(createElement('p', '', bodyLines.join(' ').replace(/\*\*/g, '') || 'No report body available.'));
        fragment.append(summary);

        metaLines.forEach((line) => {
            const card = createElement('article', 'report-card');
            const [label, ...rest] = line.replace(/\*\*/g, '').split(':');
            card.append(createElement('h3', '', label.trim()));
            card.append(createElement('p', '', rest.join(':').trim() || line));
            fragment.append(card);
        });

        return fragment;
    };

    const openArtifact = async (key) => {
        const config = artifacts[key];
        if (!config || !artifactViewer || !artifactTitle || !artifactBody) return;

        artifactTitle.textContent = config.title;
        artifactBody.replaceChildren(createElement('p', 'artifact-loading', 'Loading artifact preview...'));
        artifactViewer.classList.add('open');
        artifactViewer.setAttribute('aria-hidden', 'false');
        document.body.classList.add('artifact-open');
        artifactClose?.focus();

        try {
            const response = await fetch(config.source);
            if (!response.ok) throw new Error(`Artifact not found: ${config.source}`);

            const content = config.type === 'report' ? await response.text() : await response.json();
            const rendered = config.type === 'shortlist'
                ? renderShortlist(content)
                : config.type === 'results'
                    ? renderResults(content)
                    : renderReport(content);
            artifactBody.replaceChildren(rendered);
        } catch (error) {
            artifactBody.replaceChildren(createElement('p', 'artifact-error', error.message || 'Unable to load artifact preview.'));
        }
    };

    const closeArtifact = () => {
        if (!artifactViewer) return;
        artifactViewer.classList.remove('open');
        artifactViewer.setAttribute('aria-hidden', 'true');
        document.body.classList.remove('artifact-open');
    };

    document.querySelectorAll('[data-artifact]').forEach((button) => {
        button.addEventListener('click', () => openArtifact(button.getAttribute('data-artifact')));
    });

    artifactClose?.addEventListener('click', closeArtifact);
    artifactViewer?.addEventListener('click', (event) => {
        if (event.target === artifactViewer) closeArtifact();
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') closeArtifact();
    });

    revealTargets.forEach((target) => target.classList.add('reveal'));

    if (reduceMotion || !('IntersectionObserver' in window)) {
        revealTargets.forEach((target) => target.classList.add('active'));
    } else {
        const revealObserver = new IntersectionObserver((entries, observer) => {
            entries.forEach((entry) => {
                if (entry.isIntersecting) {
                    entry.target.classList.add('active');
                    observer.unobserve(entry.target);
                }
            });
        }, { rootMargin: '0px 0px -10% 0px', threshold: 0.12 });

        revealTargets.forEach((target) => revealObserver.observe(target));
    }

    const sections = Array.from(document.querySelectorAll('main section[id]'));
    if ('IntersectionObserver' in window && sections.length > 0) {
        const navObserver = new IntersectionObserver((entries) => {
            entries.forEach((entry) => {
                if (!entry.isIntersecting) return;
                navLinks.forEach((link) => {
                    link.classList.toggle('active', link.getAttribute('href') === `#${entry.target.id}`);
                });
            });
        }, { rootMargin: '-42% 0px -48% 0px', threshold: 0.01 });

        sections.forEach((section) => navObserver.observe(section));
    }
});
