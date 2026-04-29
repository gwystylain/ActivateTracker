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
        const byDate = new Map(p.points.map(pt => [pt.date, pt]));
        // Forward-fill the whole point object so the tooltip can find a
        // location breakdown even on days this player wasn't polled.
        let last = null;
        const data = labels.map(d => {
            if (byDate.has(d)) last = byDate.get(d);
            return last ? { x: d, y: last.total_score, locations: last.locations } : null;
        });
        return {
            label: p.display_name,
            data,
            borderColor: palette[i % palette.length],
            backgroundColor: palette[i % palette.length] + '33',
            tension: 0.15,
            spanGaps: true,
            pointRadius: 2,
            parsing: false,
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
                            const locs = ctx.raw && ctx.raw.locations;
                            if (!locs) return '';
                            const lines = Object.entries(locs).map(
                                ([slug, score]) => `  ${slug}: ${fmt.format(score)}`
                            );
                            return lines;
                        },
                    },
                },
            },
        },
    });
})();
