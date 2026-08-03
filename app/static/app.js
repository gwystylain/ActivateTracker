// Collapsible per-player breakdown rows on the dashboard summary table.
(function () {
    function toggle(row) {
        const id = row.dataset.player;
        const expanded = !row.classList.contains('expanded');
        row.classList.toggle('expanded', expanded);
        row.setAttribute('aria-expanded', expanded ? 'true' : 'false');
        document
            .querySelectorAll('tr.loc-row[data-player="' + id + '"]')
            .forEach(lr => lr.classList.toggle('show', expanded));
    }

    document.querySelectorAll('table.player-summary tr.player-row').forEach(row => {
        row.addEventListener('click', () => toggle(row));
        row.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggle(row);
            }
        });
    });
})();

(async function () {
    const canvas = document.getElementById('scoresChart');
    if (!canvas || typeof Chart === 'undefined') return;

    const palette = [
        '#58a6ff', '#f78166', '#3fb950', '#d2a8ff',
        '#ffa657', '#79c0ff', '#ff7b72', '#a5d6ff',
    ];

    let payload;
    try {
        const resp = await fetch('/api/chart-data', { credentials: 'same-origin' });
        if (!resp.ok) throw new Error('chart-data ' + resp.status);
        payload = await resp.json();
    } catch (e) {
        canvas.replaceWith(Object.assign(document.createElement('p'), {
            className: 'muted',
            textContent: 'Failed to load chart data.',
        }));
        return;
    }

    const players = payload.players || [];

    // Build the union of all dates as ordered labels.
    const dateSet = new Set();
    for (const p of players) for (const pt of p.points) dateSet.add(pt.date);
    const labels = Array.from(dateSet).sort();

    const fmt = new Intl.NumberFormat();

    // The two things a point can plot. Every payload point carries both, so
    // switching between them is a re-read of data already in hand — no refetch.
    const METRICS = {
        top: {
            key: 'top_score',
            caption: "Each line is the player's highest single-location score.",
        },
        total: {
            key: 'total_score',
            caption: "Each line is the sum of the player's location scores.",
        },
    };
    const STORAGE_KEY = 'atrk.chartMetric';

    // Dates this player's chosen metric actually moved. Only these get a
    // visible dot; the rest are forward-filled for tooltip continuity and
    // drawn at radius 0. Derived per metric because the API emits a point when
    // *either* metric moved — in "best location" mode, a day where only a
    // non-leading location gained is a flat, dotless stretch of line.
    function changeDates(points, key) {
        const dates = new Set();
        let prev = null;
        for (const pt of points) {
            if (prev === null || pt[key] !== prev) dates.add(pt.date);
            prev = pt[key];
        }
        return dates;
    }

    function seriesFor(p, key) {
        const changed = changeDates(p.points, key);
        const byDate = new Map(p.points.map(pt => [pt.date, pt]));
        let last = null;
        const data = labels.map(d => {
            if (byDate.has(d)) last = byDate.get(d);
            // With parsing:false every entry must be an object; a bare null
            // throws when Chart.js reads .x/.y off it. Use a null-y gap point
            // for dates before this player's first observation (e.g. a player
            // added after the graph already has history). spanGaps bridges it.
            return last
                ? { x: d, y: last[key], locations: last.locations }
                : { x: d, y: null };
        });
        const pointRadius = labels.map(d => (changed.has(d) ? 3 : 0));
        return {
            data,
            pointRadius,
            pointHoverRadius: pointRadius.map(r => (r > 0 ? r + 2 : 0)),
        };
    }

    // Reading localStorage throws outright when storage is blocked, which would
    // take the whole chart down with it — fall back to the default instead.
    let stored = null;
    try {
        stored = localStorage.getItem(STORAGE_KEY);
    } catch (e) { /* storage unavailable */ }
    let metric = METRICS[stored] ? stored : 'top';

    const datasets = players.map((p, i) => ({
        label: p.display_name,
        ...seriesFor(p, METRICS[metric].key),
        borderColor: palette[i % palette.length],
        backgroundColor: palette[i % palette.length] + '33',
        tension: 0.15,
        spanGaps: true,
        parsing: false,
    }));

    const chart = new Chart(canvas, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'nearest', intersect: false },
            scales: {
                x: { ticks: { color: '#8b949e', maxTicksLimit: 12 },
                     grid: { color: '#30363d' } },
                y: { ticks: { color: '#8b949e' }, grid: { color: '#30363d' },
                     beginAtZero: false },
            },
            plugins: {
                legend: {
                    position: 'right',
                    align: 'start',
                    labels: { color: '#e6edf3', boxWidth: 14 },
                },
                tooltip: {
                    mode: 'nearest',
                    intersect: false,
                    callbacks: {
                        label(ctx) {
                            return `${ctx.dataset.label}: ${fmt.format(ctx.parsed.y)}`;
                        },
                        afterLabel(ctx) {
                            const locs = ctx.raw && ctx.raw.locations;
                            if (!locs) return '';
                            const lines = Object.entries(locs)
                                .filter(([, score]) => score > 0)
                                .map(([slug, score]) => `  ${slug}: ${fmt.format(score)}`);
                            return lines;
                        },
                    },
                },
            },
        },
    });

    const caption = document.getElementById('chartCaption');
    const toggle = document.getElementById('metricToggle');

    function applyMetric(next) {
        metric = next;
        const key = METRICS[metric].key;
        players.forEach((p, i) => Object.assign(chart.data.datasets[i], seriesFor(p, key)));
        // A tooltip left open across the switch keeps rendering the old
        // metric's number until the pointer moves — dismiss it with the swap.
        chart.setActiveElements([]);
        chart.tooltip.setActiveElements([], { x: 0, y: 0 });
        chart.update();
        if (caption) {
            caption.textContent =
                METRICS[metric].caption + ' Hover a point for the per-location breakdown.';
        }
        if (toggle) {
            toggle.querySelectorAll('button[data-metric]').forEach(b => {
                b.setAttribute('aria-pressed', b.dataset.metric === metric ? 'true' : 'false');
            });
        }
        // Safari in private mode throws on write; the toggle still works, the
        // choice just won't survive a reload.
        try {
            localStorage.setItem(STORAGE_KEY, metric);
        } catch (e) { /* non-fatal */ }
    }

    if (toggle) {
        toggle.addEventListener('click', e => {
            const btn = e.target.closest('button[data-metric]');
            if (btn && btn.dataset.metric !== metric) applyMetric(btn.dataset.metric);
        });
    }
    applyMetric(metric);  // sync caption + pressed state with the restored choice
})();
