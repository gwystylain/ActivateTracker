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

    const datasets = players.map((p, i) => {
        // Dates this player's score actually moved. Used to decide which
        // x positions get a visible dot — others are forward-filled for
        // tooltip continuity but rendered with radius 0.
        const changeDates = new Set(p.points.map(pt => pt.date));
        const byDate = new Map(p.points.map(pt => [pt.date, pt]));
        // Plain numeric data array (compatible with the category x-axis) plus
        // a parallel side-channel for the per-location breakdown. The tooltip
        // looks up locationsByIndex[ctx.dataIndex] rather than relying on
        // Chart.js to carry custom fields through the parsing pipeline.
        let last = null;
        const data = [];
        const locationsByIndex = [];
        labels.forEach(d => {
            if (byDate.has(d)) last = byDate.get(d);
            data.push(last ? last.total_score : null);
            locationsByIndex.push(last ? last.locations : null);
        });
        const pointRadius = labels.map(d => (changeDates.has(d) ? 3 : 0));
        const pointHoverRadius = pointRadius.map(r => (r > 0 ? r + 2 : 0));
        return {
            label: p.display_name,
            data,
            locationsByIndex,
            borderColor: palette[i % palette.length],
            backgroundColor: palette[i % palette.length] + '33',
            tension: 0.15,
            spanGaps: true,
            pointRadius,
            pointHoverRadius,
        };
    });

    new Chart(canvas, {
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
                legend: { labels: { color: '#e6edf3' } },
                tooltip: {
                    mode: 'nearest',
                    intersect: false,
                    callbacks: {
                        label(ctx) {
                            const total = ctx.parsed.y;
                            return `${ctx.dataset.label}: ${fmt.format(total)}`;
                        },
                        afterLabel(ctx) {
                            const lookup = ctx.dataset.locationsByIndex;
                            const locs = lookup && lookup[ctx.dataIndex];
                            if (!locs) return '';
                            return Object.entries(locs).map(
                                ([slug, score]) => `  ${slug}: ${fmt.format(score)}`
                            );
                        },
                    },
                },
            },
        },
    });
})();
