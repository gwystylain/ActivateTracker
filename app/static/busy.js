// /busy — which upcoming evening is quietest at the chosen time.
//
// The server sends, per day and per 15-minute arrival, the most people booked
// onto the floor at any moment of a 60- or a 90-minute visit starting then —
// booked now, and projected with the late bookings still to come. Everything
// here is picking numbers out of that, so changing the time, the length or the
// days never refetches; changing the venue fetches that venue once.
(() => {
    'use strict';

    const STORE = 'atrk.busy';
    const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    // Arrival times either side of the chosen one in each row's strip: enough
    // to see whether coming half an hour earlier or later would be quieter.
    const CONTEXT = 90;
    // Strip shades are relative to the busiest cell shown, but never to less
    // than this many people, so a near-empty week doesn't paint one booked pair
    // at full strength.
    const MIN_SHADE = 10;

    const $ = id => document.getElementById(id);
    const venueSel = $('busyVenue');
    const timeIn = $('busyTime');
    const lengthGroup = $('busyLength');
    const daysBox = $('busyDays');
    const statusEl = $('busyStatus');
    const card = $('busyCard');
    const answerEl = $('busyAnswer');
    const axisEl = $('busyAxis');
    const rowsEl = $('busyRows');
    const noteEl = $('busyNote');
    const tip = $('busyTip');
    const collecting = statusEl.dataset.enabled === 'true';

    const cache = new Map();

    // ---- state, remembered per browser ----
    // Blocked storage throws on access, so both directions are wrapped: a
    // private window still gets a working page, just not a remembered one.
    const state = {
        venue: venueSel.value,
        time: timeIn.value,
        minutes: 90,
        days: [0, 1, 2, 3],
    };
    try {
        const saved = JSON.parse(localStorage.getItem(STORE) || '{}');
        if ([...venueSel.options].some(o => o.value === saved.venue)) state.venue = saved.venue;
        if (/^\d\d:\d\d$/.test(saved.time || '')) state.time = saved.time;
        if (saved.minutes === 60 || saved.minutes === 90) state.minutes = saved.minutes;
        if (Array.isArray(saved.days)) state.days = saved.days.filter(d => d >= 0 && d <= 6);
    } catch (e) { /* defaults stand */ }

    function save() {
        try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) { /* not remembered */ }
    }

    venueSel.value = state.venue;
    timeIn.value = state.time;
    for (const b of lengthGroup.querySelectorAll('button')) {
        b.setAttribute('aria-pressed', String(Number(b.dataset.minutes) === state.minutes));
    }
    for (const box of daysBox.querySelectorAll('input')) {
        box.checked = state.days.includes(Number(box.value));
    }

    // ---- time and date helpers ----
    const toMin = hhmm => {
        const [h, m] = hhmm.split(':').map(Number);
        return h * 60 + m;
    };
    // Sessions start on the quarter hour; arriving at 5:50 means the 6:00 one.
    const ceil15 = m => Math.ceil(m / 15) * 15;
    function clock(mins) {
        let h = Math.floor(mins / 60) % 24;
        const m = mins % 60;
        const ap = h >= 12 ? 'pm' : 'am';
        h = h % 12 || 12;
        return `${h}:${String(m).padStart(2, '0')} ${ap}`;
    }
    // Dates are the venue's calendar days, so they are handled as UTC
    // midnights: the viewer's own time zone must never shift one by a day.
    const parseDay = iso => new Date(iso + 'T00:00:00Z');
    const isoDay = d => d.toISOString().slice(0, 10);
    const weekday = d => (d.getUTCDay() + 6) % 7;   // Monday = 0, as Python has it
    const people = n => `${n} booked player${n === 1 ? '' : 's'}`;

    // ---- the numbers ----
    // {now, exp} for a visit of the chosen length arriving at `t`, or null
    // where the checkout sells no such visit.
    function visitAt(row, t, w) {
        const s = row.steps.get(t);
        if (!s || s.now[w] == null) return null;
        return { now: s.now[w], exp: s.exp[w] };
    }

    function buildRows(data) {
        const today = parseDay(data.now_local.slice(0, 10));
        const nowMin = toMin(data.now_local.slice(11, 16));
        const arrive = ceil15(toMin(state.time));
        const w = data.windows.indexOf(state.minutes);
        const byDate = new Map(data.days.map(d => [d.date, d]));
        const rows = [];
        for (let i = 0; i < 7; i += 1) {
            const date = new Date(today.getTime() + i * 86400000);
            const wd = weekday(date);
            if (!state.days.includes(wd)) continue;
            const day = byDate.get(date.toISOString().slice(0, 10));
            const row = { iso: isoDay(date), date, wd, offset: i, day, status: 'ok' };
            if (!day) {
                row.status = 'nodata';
            } else {
                row.steps = new Map(day.steps.map(s => [toMin(s.t), s]));
                row.visit = visitAt(row, arrive, w);
                if (i === 0 && arrive <= nowMin) row.status = 'past';
                else if (!row.visit) row.status = 'closed';
            }
            rows.push(row);
        }
        return rows;
    }

    // ---- rendering ----
    function dayName(row) {
        if (row.offset === 0) return 'Today';
        if (row.offset === 1) return 'Tomorrow';
        return DAYS[row.wd];
    }

    function dateLabel(row) {
        return `${MONTHS[row.date.getUTCMonth()]} ${row.date.getUTCDate()}`;
    }

    function render() {
        hideTip();
        const data = cache.get(state.venue);
        if (!data) return;
        const venueName = venueSel.selectedOptions[0]?.textContent || state.venue;

        if (!data.days.length) {
            card.hidden = true;
            statusEl.hidden = false;
            statusEl.textContent = collecting
                ? `No booking data for ${venueName} yet. It arrives with the next booking poll.`
                : `No booking data for ${venueName}: booking polling is off (bookings.enabled in config.yaml).`;
            return;
        }
        statusEl.hidden = true;
        card.hidden = false;

        const w = data.windows.indexOf(state.minutes);
        const arrive = ceil15(toMin(state.time));
        const range = `${clock(arrive)} to ${clock(arrive + state.minutes)}`;
        const rows = buildRows(data);
        const open = rows.filter(r => r.status === 'ok');

        // The answer. Ties are by the rounded number, since that's all the
        // page shows: two evenings both reading "about 6" are equally quiet.
        const rounded = r => Math.round(r.visit.exp);
        const least = open.length ? Math.min(...open.map(rounded)) : null;
        const quietest = open.filter(r => rounded(r) === least);
        const decided = quietest.length < open.length || open.length === 1;
        answerEl.textContent = '';
        if (!state.days.length) {
            answerEl.textContent = 'Pick at least one day.';
        } else if (!open.length) {
            answerEl.textContent = `None of the chosen days in the next week has a ${state.minutes}-minute session at ${clock(arrive)}.`;
        } else if (!decided) {
            answerEl.textContent = least === 0
                ? `Nothing is booked from ${range} on any of these days yet.`
                : `All of these look about the same: at most around ${people(least)} on the floor at once.`;
        } else {
            // Without projections a far-off evening wins just by being far
            // off, so the answer itself says how much it's worth.
            const soFar = open.some(r => !r.day.projected) ? ' so far' : '';
            const strong = document.createElement('strong');
            strong.textContent = quietest.map(dayName).join(' and ');
            answerEl.append(
                strong,
                ` ${quietest.length > 1 ? 'look' : 'looks'} quietest${soFar}: at most about ${people(least)} on the floor at once, ${range}.`,
            );
        }

        const cells = [];
        for (let t = arrive - CONTEXT; t <= arrive + CONTEXT; t += 15) cells.push(t);
        let shade = MIN_SHADE;
        for (const r of open) {
            for (const t of cells) {
                const v = visitAt(r, t, w);
                if (v) shade = Math.max(shade, v.exp);
            }
        }

        renderAxis(cells, arrive);
        const opt = {
            w, cells, arrive, range, shade,
            capacity: data.capacity || Math.max(1, ...open.map(r => r.visit.exp)),
        };
        rowsEl.replaceChildren(...rows.map(r => renderRow(r, {
            ...opt, quiet: decided && quietest.includes(r),
        })));

        const notes = [];
        if (data.as_of_local) {
            const asOf = parseDay(data.as_of_local.slice(0, 10));
            notes.push(`Bookings as of ${DAYS[weekday(asOf)].slice(0, 3)} ${clock(toMin(data.as_of_local.slice(11, 16)))}`
                + (data.capacity ? `, on a floor that holds ${data.capacity}.` : '.'));
        }
        notes.push('Only counts what\u2019s in Activate\u2019s booking system.');
        if (open.some(r => !r.day.projected)) {
            notes.push('Late bookings aren\u2019t projected yet (that needs a couple of days of history), so days further out will fill more than shown.');
        }
        noteEl.textContent = notes.join(' ');
    }

    function renderAxis(cells, arrive) {
        const n = cells.length;
        const label = (t, cls, left) => {
            const span = document.createElement('span');
            span.className = cls;
            span.textContent = clock(t);
            span.style.left = left;
            return span;
        };
        // Each cell is one arrival time: the chosen one is labelled at its
        // centre, the ends flush with the strip's edges.
        const track = document.createElement('div');
        track.className = 'busy-axis-track';
        track.append(
            label(cells[0], 'edge-start', '0%'),
            label(arrive, 'mark', `${((cells.indexOf(arrive) + 0.5) / n) * 100}%`),
            label(cells[n - 1], 'edge-end', '100%'),
        );
        const title = document.createElement('span');
        title.textContent = 'Arriving at';
        axisEl.replaceChildren(title, track);
    }

    function renderRow(r, opt) {
        const row = document.createElement('div');
        row.className = 'busy-row';
        row.setAttribute('role', 'listitem');
        if (opt.quiet) row.classList.add('quietest');
        if (r.status !== 'ok') row.classList.add('inactive');

        const label = document.createElement('div');
        label.className = 'busy-day';
        const strong = document.createElement('strong');
        strong.textContent = dayName(r);
        label.append(strong);
        if (opt.quiet) {
            const tag = document.createElement('span');
            tag.className = 'busy-tag';
            tag.textContent = 'Quietest';
            label.append(tag);
        }
        const date = document.createElement('span');
        date.className = 'busy-date muted';
        date.textContent = r.offset > 1 ? dateLabel(r) : `${DAYS[r.wd].slice(0, 3)} ${dateLabel(r)}`;
        label.append(date);

        const plot = document.createElement('div');
        plot.className = 'busy-plot';
        const value = document.createElement('div');
        value.className = 'busy-value';

        if (r.status !== 'ok') {
            const why = {
                nodata: 'No booking data',
                past: 'Already past',
                closed: `No ${state.minutes}-minute session at ${clock(opt.arrive)}`,
            }[r.status];
            value.textContent = '\u2014';
            const msg = document.createElement('span');
            msg.className = 'muted small';
            msg.textContent = why;
            plot.append(msg);
            row.setAttribute('aria-label', `${dayName(r)} ${dateLabel(r)}: ${why}`);
            row.append(label, plot, value);
            return row;
        }

        const nowN = Math.round(r.visit.now);
        const expN = Math.round(r.visit.exp);
        const tipText = [
            `${DAYS[r.wd]} ${dateLabel(r)}, ${opt.range}`,
            `Most on the floor at once, booked so far: ${nowN}`,
            `Expected by then: ${expN} of ${opt.capacity}`,
        ].join('\n');

        const bar = document.createElement('div');
        bar.className = 'busy-bar';
        bar.tabIndex = 0;
        bar.dataset.tip = tipText;
        bar.setAttribute('aria-label', tipText.replace(/\n/g, '. '));
        // Scaled to the floor's capacity, so a bar's length is how full the
        // place is. Zero-width segments are left out entirely, or the gap
        // between segments would still push the bar 2px off its baseline.
        const segs = [['seg-now', r.visit.now], ['seg-more', Math.max(0, r.visit.exp - r.visit.now)]]
            .filter(([, v]) => v / opt.capacity >= 0.005)
            .map(([cls, v]) => {
                const seg = document.createElement('span');
                seg.className = `seg ${cls}`;
                seg.style.width = `${Math.min(100, (v / opt.capacity) * 100)}%`;
                return seg;
            });
        if (segs.length) segs[segs.length - 1].classList.add('end');
        bar.append(...segs);

        const strip = document.createElement('div');
        strip.className = 'busy-strip';
        strip.style.gridTemplateColumns = `repeat(${opt.cells.length}, 1fr)`;
        opt.cells.forEach((t, i) => {
            const cell = document.createElement('span');
            cell.className = 'cell';
            const v = visitAt(r, t, opt.w);
            if (!v) {
                cell.classList.add('shut');
                cell.dataset.tip = `Arriving ${clock(t)}: no ${state.minutes}-minute session`;
            } else {
                cell.style.setProperty('--load', String(Math.min(1, v.exp / opt.shade)));
                const e = Math.round(v.exp);
                const n = Math.round(v.now);
                cell.dataset.tip = `Arriving ${clock(t)}: at most about ${e} on the floor`
                    + (n !== e ? ` (${n} booked so far)` : '');
            }
            if (t === opt.arrive) cell.classList.add('chosen');
            cell.style.gridColumn = String(i + 1);
            strip.append(cell);
        });
        plot.append(bar, strip);

        const big = document.createElement('strong');
        big.textContent = `\u2248 ${expN}`;
        const detail = document.createElement('span');
        detail.className = 'muted small';
        detail.textContent = expN > nowN ? `${nowN} booked, +${expN - nowN} likely` : `${nowN} booked`;
        value.append(big, detail);

        row.append(label, plot, value);
        return row;
    }

    // ---- tooltip ----
    // One shared bubble, refilled per target; delegated because every row is
    // rebuilt on each render.
    let tipAnchor = null;

    function showTip(anchor) {
        tip.textContent = anchor.dataset.tip;
        tip.hidden = false;
        tipAnchor = anchor;
        const r = anchor.getBoundingClientRect();
        const gap = 8;
        let left = Math.min(r.left, window.innerWidth - tip.offsetWidth - gap);
        if (left < gap) left = gap;
        let top = r.bottom + gap;
        if (top + tip.offsetHeight > window.innerHeight - gap && r.top - gap - tip.offsetHeight > gap) {
            top = r.top - gap - tip.offsetHeight;
        }
        tip.style.left = `${left}px`;
        tip.style.top = `${top}px`;
    }

    function hideTip() {
        if (tip.hidden) return;
        tip.hidden = true;
        tipAnchor = null;
    }

    rowsEl.addEventListener('mouseover', e => {
        const target = e.target.closest('[data-tip]');
        if (target && target !== tipAnchor) showTip(target);
        else if (!target) hideTip();
    });
    rowsEl.addEventListener('mouseleave', hideTip);
    rowsEl.addEventListener('focusin', e => {
        const target = e.target.closest('[data-tip]');
        if (target) showTip(target);
    });
    rowsEl.addEventListener('focusout', hideTip);
    window.addEventListener('scroll', hideTip, { passive: true });

    // ---- wiring ----
    async function ensure(venue) {
        if (cache.has(venue)) return;
        statusEl.hidden = false;
        statusEl.textContent = 'Loading\u2026';
        // The previous venue's answer stays up until this one lands, dimmed so
        // it can't be read as this venue's.
        card.classList.add('stale');
        try {
            const resp = await fetch(`/api/busy-forecast?venue=${encodeURIComponent(venue)}`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            cache.set(venue, await resp.json());
        } catch (e) {
            card.hidden = true;
            statusEl.textContent = 'Couldn\u2019t load the forecast.';
            throw e;
        } finally {
            card.classList.remove('stale');
        }
    }

    async function refresh() {
        try {
            await ensure(state.venue);
        } catch (e) {
            return;
        }
        render();
    }

    venueSel.addEventListener('change', () => {
        state.venue = venueSel.value;
        save();
        refresh();
    });
    timeIn.addEventListener('input', () => {
        if (!/^\d\d:\d\d$/.test(timeIn.value)) return;
        state.time = timeIn.value;
        save();
        render();
    });
    lengthGroup.addEventListener('click', e => {
        const b = e.target.closest('button[data-minutes]');
        if (!b) return;
        state.minutes = Number(b.dataset.minutes);
        for (const other of lengthGroup.querySelectorAll('button')) {
            other.setAttribute('aria-pressed', String(other === b));
        }
        save();
        render();
    });
    daysBox.addEventListener('change', () => {
        state.days = [...daysBox.querySelectorAll('input:checked')].map(b => Number(b.value));
        save();
        render();
    });

    refresh();
})();
