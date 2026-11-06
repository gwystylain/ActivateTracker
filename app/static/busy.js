// /busy — which upcoming evening is quietest at the chosen time.
//
// The server sends, per day and per 15-minute arrival, the most people booked
// onto the floor at any moment of a 60-, 90- or 120-minute visit starting then
// — booked now, and projected with the late bookings still to come. Everything
// here is picking numbers out of that for the next seven days, so changing the
// time or the length never refetches; changing the venue fetches that venue
// once. Scrolling over a strip, or swiping, moves the arrival time itself.
(() => {
    'use strict';

    const STORE = 'atrk.busy';
    const DAYS = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
    const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    // Arrival times either side of the chosen one in each row's strip: enough
    // to see whether coming half an hour earlier or later would be quieter.
    const CONTEXT = 90;
    // Strip shades are relative to the week's busiest arrival at this length,
    // so a cell keeps its colour as the strip moves, but never to less than
    // this many people, so a near-empty week doesn't paint one booked pair at
    // full strength.
    const MIN_SHADE = 10;
    // Wheel travel per 15-minute step. A mouse notch is about 100 and moves
    // exactly one step however far past this it goes; a trackpad sends many
    // small deltas, which add up to about a cell's width.
    const WHEEL_STEP = 40;
    // A wheel that has been scrolling the page keeps doing so until it has
    // paused this long, so a strip passing under the pointer doesn't catch it.
    const WHEEL_LATCH_MS = 200;

    const $ = id => document.getElementById(id);
    const venueSel = $('busyVenue');
    const timeIn = $('busyTime');
    const lengthGroup = $('busyLength');
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
    };
    try {
        const saved = JSON.parse(localStorage.getItem(STORE) || '{}');
        if ([...venueSel.options].some(o => o.value === saved.venue)) state.venue = saved.venue;
        if (/^\d\d:\d\d$/.test(saved.time || '')) state.time = saved.time;
        if ([60, 90, 120].includes(saved.minutes)) state.minutes = saved.minutes;
    } catch (e) { /* defaults stand */ }

    function save() {
        try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) { /* not remembered */ }
    }

    venueSel.value = state.venue;
    timeIn.value = state.time;
    for (const b of lengthGroup.querySelectorAll('button')) {
        b.setAttribute('aria-pressed', String(Number(b.dataset.minutes) === state.minutes));
    }

    // ---- time and date helpers ----
    const toMin = hhmm => {
        const [h, m] = hhmm.split(':').map(Number);
        return h * 60 + m;
    };
    // Sessions start on the quarter hour; arriving at 5:50 means the 6:00 one.
    const ceil15 = m => Math.ceil(m / 15) * 15;
    const hhmm = m => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
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
    // where no such visit fits.
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
        const nowMin = toMin(data.now_local.slice(11, 16));
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
        if (!open.length) {
            answerEl.textContent = `No day in the next week fits a ${state.minutes}-minute visit at ${clock(arrive)}.`;
        } else if (!decided) {
            answerEl.textContent = least === 0
                ? `Nothing is booked from ${range} on any day this week yet.`
                : `Every day this week looks about the same: at most around ${people(least)} on the floor at once.`;
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
        for (const r of rows) {
            for (const [t, s] of r.steps || []) {
                if (s.exp[w] != null && !(r.offset === 0 && t <= nowMin)) shade = Math.max(shade, s.exp[w]);
            }
        }

        renderAxis(cells, arrive);
        const opt = {
            w, cells, arrive, nowMin, range, shade,
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
                closed: `No ${state.minutes}-minute visit at ${clock(opt.arrive)}`,
            }[r.status];
            value.textContent = '\u2014';
            const msg = document.createElement('span');
            msg.className = 'busy-why muted small';
            msg.textContent = why;
            plot.append(msg);
            row.setAttribute('aria-label', `${dayName(r)} ${dateLabel(r)}: ${why}`);
            // A day with nothing at this arrival still has the rest of its
            // hours, and this may be the strip being scrolled: it can't vanish
            // from under the pointer.
            if (r.steps) plot.append(renderStrip(r, opt));
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
        plot.append(bar, renderStrip(r, opt));

        const big = document.createElement('strong');
        big.textContent = `\u2248 ${expN}`;
        const detail = document.createElement('span');
        detail.className = 'muted small';
        detail.textContent = expN > nowN ? `${nowN} booked, +${expN - nowN} likely` : `${nowN} booked`;
        value.append(big, detail);

        row.append(label, plot, value);
        return row;
    }

    function renderStrip(r, opt) {
        const strip = document.createElement('div');
        strip.className = 'busy-strip';
        strip.style.gridTemplateColumns = `repeat(${opt.cells.length}, 1fr)`;
        opt.cells.forEach((t, i) => {
            const cell = document.createElement('span');
            cell.className = 'cell';
            const v = visitAt(r, t, opt.w);
            if (r.offset === 0 && t <= opt.nowMin) {
                cell.classList.add('shut');
                cell.dataset.tip = `Arriving ${clock(t)}: already past`;
            } else if (!v) {
                cell.classList.add('shut');
                cell.dataset.tip = `Arriving ${clock(t)}: no ${state.minutes}-minute visit`;
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
        return strip;
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

    // ---- moving through the day ----
    // There is one arrival time on the page. A scroll or a swipe changes it and
    // the bars, the answer and the time box all follow, rather than panning a
    // strip away from what they describe.
    function arrivalRange(data) {
        const w = data.windows.indexOf(state.minutes);
        const starts = data.days.flatMap(d => d.steps.filter(s => s.now[w] != null).map(s => toMin(s.t)));
        return starts.length ? { first: Math.min(...starts), last: Math.max(...starts) } : null;
    }

    function moveArrival(steps) {
        const data = cache.get(state.venue);
        const range = data && arrivalRange(data);
        if (!range) return;
        const arrive = ceil15(toMin(state.time));
        const next = Math.min(range.last, Math.max(range.first, arrive + steps * 15));
        if (next === arrive) return;
        state.time = hhmm(next);
        timeIn.value = state.time;
        save();
        render();
    }

    let wheelTravel = 0;
    let pageWheelAt = -Infinity;
    rowsEl.addEventListener('wheel', e => {
        // Ctrl is the browser's zoom, which is also what a trackpad pinch sends.
        if (e.ctrlKey || !e.target.closest('.busy-strip')) return;
        if (e.timeStamp - pageWheelAt < WHEEL_LATCH_MS) return;
        e.preventDefault();
        // Whichever axis moved more, so a trackpad's sideways swipe counts too.
        // Firefox reports a mouse wheel in lines, not pixels.
        const delta = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
        wheelTravel += delta * ([1, 33, 400][e.deltaMode] ?? 1);
        if (Math.abs(wheelTravel) < WHEEL_STEP) return;
        moveArrival(Math.sign(wheelTravel));
        wheelTravel = 0;
    }, { passive: false });
    // After the listener above, so it can tell which of the two this one was.
    window.addEventListener('wheel', e => {
        if (!e.defaultPrevented) pageWheelAt = e.timeStamp;
    }, { passive: true });

    // A finger can start anywhere on a row: `touch-action` leaves vertical
    // swipes to the browser, and a tap never travels a whole cell. A mouse only
    // drags from the strip, so the text elsewhere stays selectable. Dragging
    // left moves the strip left, which brings later times in.
    let swipe = null;
    rowsEl.addEventListener('pointerdown', e => {
        if (e.button !== 0) return;
        if (e.pointerType === 'mouse' && !e.target.closest('.busy-strip')) return;
        const strip = rowsEl.querySelector('.busy-strip');
        if (strip) swipe = { id: e.pointerId, x: e.clientX, cell: strip.clientWidth / strip.children.length };
    });
    rowsEl.addEventListener('pointermove', e => {
        if (!swipe || e.pointerId !== swipe.id) return;
        // A mouse released outside the rows never sent its pointerup here.
        if (!e.buttons) {
            swipe = null;
            return;
        }
        const steps = Math.trunc((swipe.x - e.clientX) / swipe.cell);
        if (!steps) return;
        swipe.x -= steps * swipe.cell;
        // Every render replaces the element under the pointer, and events aimed
        // at a removed element reach nothing, so they are held on the container
        // first. Not at pointerdown: a tap must still land on what it touched.
        rowsEl.setPointerCapture(e.pointerId);
        moveArrival(steps);
    });
    for (const type of ['pointerup', 'pointercancel']) {
        rowsEl.addEventListener(type, e => {
            if (swipe && e.pointerId === swipe.id) swipe = null;
        });
    }

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

    refresh();
})();
