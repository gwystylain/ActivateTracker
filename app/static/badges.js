// /badges — who holds which badges, and what each player is closest to earning.
//
// Badges are per player, not per location: the badge API is keyed on the handle
// alone, so unlike /games there is nothing to select by location and the page
// fetches exactly once. Every filter below re-reads data already in hand.
(function () {
    const summaryBody = document.getElementById('summaryBody');
    if (!summaryBody) return;

    const summaryNote = document.getElementById('summaryNote');
    const stateSel = document.getElementById('stateFilter');
    const roomSel = document.getElementById('roomFilter');
    const difficultySel = document.getElementById('difficultyFilter');
    const searchInput = document.getElementById('badgeSearch');
    const hereOnlyBox = document.getElementById('hereOnly');
    const playerBox = document.getElementById('playerFilter');
    const statusEl = document.getElementById('badgesStatus');
    const farmerCard = document.getElementById('farmerCard');
    const farmerBody = document.getElementById('farmerBody');
    const farmerNote = document.getElementById('farmerNote');
    const closeCard = document.getElementById('closeCard');
    const closeBody = document.getElementById('closeBody');
    const closeNote = document.getElementById('closeNote');
    const gridCard = document.getElementById('gridCard');
    const gridHead = document.getElementById('gridHead');
    const gridBody = document.getElementById('gridBody');

    const fmt = new Intl.NumberFormat();
    const KEY = 'atrk.badges';
    const CLOSE_LIMIT = 15;
    const FARM_LIMIT = 15;
    // Sentinel rather than prose, so the filter and the detail line can't drift
    // apart the way two copies of the same sentence would.
    const NOWHERE = 'No room for it at your locations';
    // The document's own scale, in its own order — alphabetical would put Very
    // Hard between Medium and Hard.
    const DIFFICULTIES = ['Easy', 'Medium', 'Hard', 'Very Hard'];

    let data = null;                     // last /api/badge-data payload
    let selectedPlayers = new Set();     // player ids (numbers)
    const expanded = new Set();          // badge_ids showing their detail row
    // null key = the order the API sent, which is already by name.
    let sort = { key: null, dir: 1 };    // dir: 1 ascending, -1 descending

    // Blocked storage throws on access; a saved filter is not worth taking the
    // page down for, so both directions swallow it (same as games.js).
    function load(name, fallback) {
        try {
            const raw = localStorage.getItem(KEY + '.' + name);
            return raw === null ? fallback : JSON.parse(raw);
        } catch (e) {
            return fallback;
        }
    }
    function save(name, value) {
        try {
            localStorage.setItem(KEY + '.' + name, JSON.stringify(value));
        } catch (e) { /* non-fatal */ }
    }

    function el(tag, opts, children) {
        const node = document.createElement(tag);
        if (opts) {
            if (opts.className) node.className = opts.className;
            // textContent throughout: badge and player names are third-party
            // strings and must never be parsed as markup.
            if (opts.text !== undefined) node.textContent = opts.text;
            if (opts.attrs) {
                for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
            }
        }
        for (const child of children || []) if (child) node.appendChild(child);
        return node;
    }

    // ---------- badge descriptions ----------
    //
    // One shared bubble refilled and moved, rather than one per cell: the grid
    // is torn down on every filter change. It hangs off <body> and is
    // position: fixed so .table-wrap's horizontal scroll can't clip it — an
    // in-cell popover would be cut off at the table edge. Same mechanism as the
    // gamemode tips on /games.

    const tip = el('div', { className: 'mode-tip', attrs: { id: 'badgeTip', role: 'tooltip' } });
    tip.hidden = true;
    document.body.appendChild(tip);
    let tipOn = null;       // element currently carrying aria-describedby
    let tipAnchor = null;   // element the bubble is placed against

    function badgeName(badge, focusable) {
        if (!badge.description) return el('span', { text: badge.name });
        const node = el('span', { className: 'mode-name', text: badge.name });
        node.dataset.desc = badge.description;
        if (focusable) node.tabIndex = 0;
        return node;
    }

    function showTip(anchor, target) {
        tip.textContent = anchor.dataset.desc;
        tip.hidden = false;
        tipAnchor = anchor;
        placeTip();

        if (tipOn && tipOn !== target) tipOn.removeAttribute('aria-describedby');
        target.setAttribute('aria-describedby', 'badgeTip');
        tipOn = target;
    }

    function placeTip() {
        const r = tipAnchor.getBoundingClientRect();
        const gap = 8;
        const h = tip.offsetHeight;
        let left = Math.min(r.left, window.innerWidth - tip.offsetWidth - gap);
        if (left < gap) left = gap;
        let top = r.bottom + gap;
        if (top + h > window.innerHeight - gap && r.top - gap - h > gap) {
            top = r.top - gap - h;
        }
        tip.style.left = left + 'px';
        tip.style.top = top + 'px';
    }

    function hideTip() {
        if (tip.hidden) return;
        if (tipOn) tipOn.removeAttribute('aria-describedby');
        tipOn = null;
        tipAnchor = null;
        tip.hidden = true;
    }

    // Delegated, since every row is re-created on each render.
    document.addEventListener('mouseover', e => {
        if (!e.target.closest || tip.contains(e.target)) return;   // reading a long one
        const name = e.target.closest('.mode-name[data-desc]');
        if (!name) {
            hideTip();
            return;
        }
        // In the grid the row is the tab stop, so it carries the description
        // rather than the name inside it — a second tab stop nested in a
        // role="button" row would misdescribe the structure.
        showTip(name, name.closest('tr.badge-row') || name);
    });
    document.addEventListener('mouseleave', hideTip);
    document.addEventListener('focusin', e => {
        if (!e.target.closest) return;
        const row = e.target.closest('tr.badge-row');
        const name = e.target.matches('.mode-name[data-desc]')
            ? e.target
            : (row && row.querySelector('.mode-name[data-desc]'));
        if (!name) {
            hideTip();
            return;
        }
        showTip(name, row || name);
    });
    document.addEventListener('focusout', hideTip);
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape') hideTip();
    });
    // A fixed bubble doesn't travel with its anchor. Capture phase, because the
    // scrolling is often .table-wrap's rather than the window's.
    function followTip() {
        if (!tipAnchor) return;
        if (tipAnchor.isConnected) placeTip();
        else hideTip();
    }
    window.addEventListener('scroll', followTip, true);
    window.addEventListener('resize', followTip);

    // ---------- reading the payload ----------

    function stateOf(playerId, badgeId) {
        const forPlayer = data.states[String(playerId)];
        return forPlayer ? forPlayer[String(badgeId)] : undefined;
    }

    function activePlayers() {
        return data.players.filter(p => selectedPlayers.has(p.id));
    }

    // The four badges Activate counts without a target (Activated, Halfway
    // Mark, Completionist, The Grand Tour) report totalProgress 0. A fraction
    // needs a denominator, so they are ranked nowhere and shown as a bare count.
    function hasTarget(st) {
        return !!st && !st.earned && st.total_progress > 0 && st.progress > 0;
    }

    function visibleBadges() {
        const want = stateSel.value;
        const room = roomSel.value;
        const difficulty = difficultySel.value;
        const needle = searchInput.value.trim().toLowerCase();
        const players = activePlayers();

        return data.badges.filter(b => {
            if (needle) {
                // Tips and notes are searched too — "what can I do in Hoops
                // tonight" is a better question than the badge's own wording.
                const hay = [
                    b.name, b.description, b.notes, b.level,
                    ...(b.tips || []), ...(b.watch_out || []), ...(b.rooms || []),
                ].join(' ').toLowerCase();
                if (!hay.includes(needle)) return false;
            }
            if (room && !(b.rooms || []).includes(room)) return false;
            if (difficulty && b.difficulty !== difficulty) return false;
            // Never hidden on a false premise: a badge somebody holds is
            // reachable whatever our room list says.
            if (hereOnlyBox.checked && whereObtainable(b) === NOWHERE
                && !earnedByAnyone(b)) return false;
            if (!want) return true;
            const states = players.map(p => stateOf(p.id, b.badge_id));
            if (want === 'earned') return states.some(st => st && st.earned);
            if (want === 'unearned') return states.some(st => st && !st.earned);
            // "Nobody has it" is about the tracked group, so it reads every
            // selected player rather than any one of them.
            if (want === 'nobody') return states.every(st => !st || !st.earned);
            return true;
        });
    }

    // ---------- sorting ----------
    //
    // One value function per column. Returning null means "no answer here", and
    // those always sink to the bottom whichever way the column is pointing —
    // ascending by room would otherwise open on a wall of dashes.

    function sortValue(key, b) {
        if (key === 'name') return b.name.toLowerCase();
        if (key === 'room') return b.rooms && b.rooms.length ? b.rooms.join('/') : null;
        // The document's own scale, not the alphabet: sorted as text, Very Hard
        // would land between Medium and Hard.
        if (key === 'difficulty') {
            const i = DIFFICULTIES.indexOf(b.difficulty);
            return i < 0 ? null : i;
        }
        // "2, 3, 4" sorts by the smallest count that works; "Depends" has no
        // number in it and so has no place on the scale.
        if (key === 'players') {
            const m = /\d+/.exec(b.players || '');
            return m ? Number(m[0]) : null;
        }
        if (key === 'stars') return typeof b.stars === 'number' ? b.stars : null;

        if (key.startsWith('p:')) return playerRank(Number(key.slice(2)), b);
        return null;
    }

    // Descending, this reads as: what they hold, then what they are closest to
    // holding, then what they have merely started, then everything untouched.
    function playerRank(playerId, b) {
        const st = stateOf(playerId, b.badge_id);
        if (!st) return null;
        if (st.earned) return 10;
        if (st.total_progress > 0 && st.progress > 0) return 1 + st.progress / st.total_progress;
        if (st.progress > 0) return 0.5;      // counted, but towards no total
        return 0;
    }

    function sorted(badges) {
        if (!sort.key) return badges;
        const key = sort.key;
        return [...badges].sort((a, b) => {
            const av = sortValue(key, a);
            const bv = sortValue(key, b);
            if (av === null || bv === null) {
                if (av === bv) return tieBreak(a, b);
                return av === null ? 1 : -1;      // nulls last, both directions
            }
            if (av < bv) return -sort.dir;
            if (av > bv) return sort.dir;
            return tieBreak(a, b);
        });
    }

    // Deterministic, so the two badges both called "Untouchable 5.0" don't swap
    // places between renders of the same sort.
    function tieBreak(a, b) {
        return a.name.localeCompare(b.name) || a.badge_id - b.badge_id;
    }

    function toggleSort(key, firstDir) {
        if (sort.key === key) sort = { key, dir: -sort.dir };
        else sort = { key, dir: firstDir };
        save('sort', sort);
        renderGrid();
    }

    function headerCell(label, key, firstDir) {
        const active = sort.key === key;
        const th = el('th', {
            className: 'sortable' + (active ? ' sorted' : ''),
            attrs: { 'aria-sort': active ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none' },
        });
        const btn = el('button', {
            className: 'sort-btn',
            attrs: { type: 'button' },
        }, [
            el('span', { text: label }),
            el('span', {
                className: 'sort-arrow',
                text: active ? (sort.dir === 1 ? '▲' : '▼') : '',
                attrs: { 'aria-hidden': 'true' },
            }),
        ]);
        btn.addEventListener('click', () => toggleSort(key, firstDir));
        th.appendChild(btn);
        return th;
    }

    // ---------- rendering ----------

    function render() {
        hideTip();
        renderSummary();
        renderFarmer();
        renderClose();
        renderGrid();
        renderStatus();
    }

    function renderSummary() {
        summaryBody.replaceChildren();
        for (const p of data.players) {
            const starsWon = data.badges.reduce((sum, b) => {
                const st = stateOf(p.id, b.badge_id);
                return sum + (st && st.earned ? (b.stars || 0) : 0);
            }, 0);
            const starsAll = data.badges.reduce((sum, b) => sum + (b.stars || 0), 0);

            summaryBody.appendChild(el('tr', null, [
                el('td', { text: p.display_name }),
                el('td', { className: 'count' }, [
                    el('strong', { text: String(p.earned) }),
                    el('span', { className: 'muted small', text: ' / ' + p.possible }),
                ]),
                el('td', null, [trophyChip(p.tier)]),
                el('td', {
                    className: 'muted small',
                    text: p.next_tier
                        ? p.to_next + ' to ' + p.next_tier
                        : 'all of them',
                }),
                el('td', {
                    className: 'muted small',
                    text: fmt.format(starsWon) + ' / ' + fmt.format(starsAll),
                }),
                el('td', { className: 'muted small', text: (p.updated_at || '').slice(0, 10) }),
            ]));
        }
        summaryNote.replaceChildren(...crossCheckNote());
    }

    // The score page's own trophyProgress tally, worked out independently of the
    // badge API. Shown only when the two disagree — that means one of them is
    // stale or wrong, and picking a winner silently would hide it.
    function crossCheckNote() {
        const off = data.players.filter(
            p => p.reported_earned !== null && p.reported_earned !== undefined
                && p.reported_earned !== p.earned
        );
        const base = 'A trophy needs 25, 50 or 75 badges; platinum needs all of them, '
            + 'and how many that is depends on which rooms your locations have.';
        if (!off.length) return [el('span', { text: base })];
        return [
            el('span', { text: base + ' ' }),
            el('span', {
                className: 'warn',
                text: off.map(p =>
                    p.display_name + ": this page counts " + p.earned
                    + ", Activate's own score page says " + p.reported_earned
                ).join('; ') + '.',
            }),
        ];
    }

    function trophyChip(tier) {
        if (!tier) return el('span', { className: 'muted', text: '—' });
        return el('span', {
            className: 'trophy trophy-' + tier,
            text: tier[0].toUpperCase() + tier.slice(1),
        });
    }

    // Where the difficulty sits on the document's scale. Unrecorded sorts after
    // everything on it — same null-last rule the sortable columns use.
    function difficultyRank(b) {
        const i = DIFFICULTIES.indexOf(b.difficulty);
        return i < 0 ? DIFFICULTIES.length : i;
    }

    // What one visit could add to the group's badge count. A cooperative badge
    // is awarded to everyone who does it, so a badge none of the selected
    // players hold is worth one each — which is what makes this a ranking and
    // not just a list of what's missing.
    function renderFarmer() {
        const players = activePlayers();
        const rows = [];
        for (const badge of visibleBadges()) {
            const missing = players.filter(p => {
                const st = stateOf(p.id, badge.badge_id);
                return !st || !st.earned;
            });
            if (missing.length) rows.push({ badge, missing });
        }
        rows.sort((a, b) =>
            b.missing.length - a.missing.length
            || difficultyRank(a.badge) - difficultyRank(b.badge)
            || tieBreak(a.badge, b.badge));

        farmerBody.replaceChildren();
        rows.slice(0, FARM_LIMIT).forEach((r, i) => {
            farmerBody.appendChild(el('tr', null, [
                el('td', { className: 'muted small', text: String(i + 1) }),
                el('td', null, [badgeName(r.badge, true)]),
                roomCell(r.badge),
                el('td', null, [difficultyChip(r.badge)]),
                el('td', { className: 'muted small', text: r.badge.players || '—' }),
                el('td', { className: 'count' }, [
                    el('strong', { text: String(r.missing.length) }),
                    // Naming them only earns its space once there is more than
                    // one player to tell apart.
                    players.length > 1
                        ? el('span', {
                            className: 'muted small',
                            text: ' ' + r.missing.map(p => p.display_name).join(', '),
                        })
                        : null,
                ]),
            ]));
        });

        farmerCard.hidden = rows.length === 0;
        const total = rows.reduce((sum, r) => sum + r.missing.length, 0);
        farmerNote.textContent = rows.length > FARM_LIMIT
            ? 'Showing the top ' + FARM_LIMIT + ' of ' + rows.length
                + ' badges with something to gain — ' + total
                + ' badges on the table in all.'
            : rows.length + ' badges with something to gain, '
                + total + ' badges on the table in all.';
    }

    function renderClose() {
        const players = activePlayers();
        const rows = [];
        for (const p of players) {
            for (const b of visibleBadges()) {
                const st = stateOf(p.id, b.badge_id);
                if (!hasTarget(st)) continue;
                rows.push({ player: p, badge: b, st, frac: st.progress / st.total_progress });
            }
        }
        rows.sort((a, b) => b.frac - a.frac || a.badge.name.localeCompare(b.badge.name));

        closeBody.replaceChildren();
        for (const r of rows.slice(0, CLOSE_LIMIT)) {
            closeBody.appendChild(el('tr', null, [
                el('td', { text: r.player.display_name }),
                el('td', null, [badgeName(r.badge, true)]),
                el('td', null, [
                    el('span', { className: 'bar' }, [
                        el('span', {
                            className: 'bar-fill',
                            attrs: { style: 'width:' + Math.round(r.frac * 100) + '%' },
                        }),
                    ]),
                    el('span', {
                        className: 'muted small',
                        text: ' ' + fmt.format(r.st.progress) + ' / ' + fmt.format(r.st.total_progress),
                    }),
                ]),
                el('td', {
                    className: 'count',
                    text: fmt.format(r.st.total_progress - r.st.progress),
                }),
            ]));
        }
        closeCard.hidden = rows.length === 0;
        closeNote.textContent = rows.length > CLOSE_LIMIT
            ? 'Showing the closest ' + CLOSE_LIMIT + ' of ' + rows.length
                + ' badges in progress.'
            : (rows.length ? rows.length + ' badges in progress.' : '');
    }

    function renderGrid() {
        // Called directly by the expand and reveal toggles, not only through
        // render(), so it dismisses the shared bubble itself — the row it was
        // anchored to is about to be destroyed.
        hideTip();
        const players = activePlayers();
        const badges = sorted(visibleBadges());
        const cols = 5 + players.length;

        // First click sorts the way the column is actually useful: names and
        // rooms A–Z, easiest and fewest-players first, but most stars and — for
        // a player — what they already hold at the top.
        gridHead.replaceChildren(el('tr', null, [
            headerCell('Badge', 'name', 1),
            headerCell('Room', 'room', 1),
            headerCell('Difficulty', 'difficulty', 1),
            headerCell('Players', 'players', 1),
            headerCell('Stars', 'stars', -1),
            ...players.map(p => headerCell(p.display_name, 'p:' + p.id, -1)),
        ]));

        gridBody.replaceChildren();
        for (const b of badges) {
            const isOpen = expanded.has(b.badge_id);
            // The whole row is the tab stop, so the name inside it must not be
            // a second one — the row carries aria-describedby for the tooltip.
            const row = el('tr', {
                className: 'badge-row' + (isOpen ? ' expanded' : ''),
                attrs: {
                    role: 'button',
                    tabindex: '0',
                    'aria-expanded': isOpen ? 'true' : 'false',
                },
            }, [
                el('td', null, [
                    el('span', { className: 'toggle', text: '▸', attrs: { 'aria-hidden': 'true' } }),
                    badgeName(b, false),
                ]),
                roomCell(b),
                el('td', null, [difficultyChip(b)]),
                el('td', { className: 'muted small', text: b.players || '—' }),
                el('td', { className: 'muted small', text: b.stars ? String(b.stars) : '—' }),
                ...players.map(p => cell(stateOf(p.id, b.badge_id))),
            ]);

            function toggle() {
                if (expanded.has(b.badge_id)) expanded.delete(b.badge_id);
                else expanded.add(b.badge_id);
                renderGrid();
            }
            row.addEventListener('click', toggle);
            row.addEventListener('keydown', e => {
                if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(); }
            });
            gridBody.appendChild(row);
            if (isOpen) gridBody.appendChild(detailRow(b, cols));
        }
        gridCard.hidden = players.length === 0;
    }

    function roomCell(b) {
        if (!b.rooms || !b.rooms.length) return el('td', { className: 'muted', text: '—' });
        // "Mega Laser or Trench" is a choice of room; The Marathon's three are
        // all required. The joiner is the whole difference, so it is spelled out.
        const joiner = b.rooms_mode === 'all' ? ' + ' : ' / ';
        return el('td', { className: 'small', text: b.rooms.join(joiner) });
    }

    // An estimated grade is shown, but never as if the document had said it —
    // starred and muted, the same treatment /games gives a player count the
    // document records without consensus.
    function difficultyChip(b) {
        const d = b.difficulty;
        if (!d) return el('span', { className: 'muted', text: '—' });
        const chip = el('span', {
            className: 'diff diff-' + d.toLowerCase().replace(/[^a-z]/g, ''),
            text: d,
        });
        if (!b.difficulty_estimated) return chip;
        return el('span', {
            className: 'diff-est',
            attrs: { title: b.difficulty_note || 'Estimated, not from the documents' },
        }, [
            chip,
            el('span', { className: 'flag', text: '*', attrs: { 'aria-hidden': 'true' } }),
            el('span', { className: 'sr-only', text: ' — estimated, not from the documents' }),
        ]);
    }

    function earnedByAnyone(b) {
        return data.players.some(p => {
            const st = stateOf(p.id, b.badge_id);
            return st && st.earned;
        });
    }

    // Which tracked locations have the room this badge needs. Worth saying
    // because the locations genuinely differ — Pipes at one and Portals at the
    // other, so the two badges both called "Untouchable 5.0" are earned in
    // different buildings.
    //
    // The negative is far weaker than the positive and is never stated as
    // "you can't get this": the room list comes from `location.rooms`, which is
    // only the *scoring* rooms, so the photo room is missing from every
    // location; and badges transfer between locations, so one earned at a venue
    // that isn't tracked here still counts. Both cases are live in this data —
    // Photobomb and Row By Row are earned badges whose rooms we cannot see.
    function whereObtainable(b) {
        if (!b.rooms || !b.rooms.length || !data.locations.length) return null;
        const ok = data.locations.filter(loc => {
            const has = r => loc.rooms.includes(r);
            return b.rooms_mode === 'all' ? b.rooms.every(has) : b.rooms.some(has);
        });
        if (!ok.length) return NOWHERE;
        if (ok.length === data.locations.length) return 'All your locations';
        return ok.map(l => l.name).join(', ') + ' only';
    }

    function detailRow(b, cols) {
        const bits = [];

        const where = whereObtainable(b);
        if (where === NOWHERE) {
            bits.push(field('Where', earnedByAnyone(b)
                // Evidence beats inference: somebody holds it, so the room is
                // reachable and our room list is simply the wrong instrument.
                ? 'Earned already, so it was done somewhere — no scoring room '
                  + 'for it at your tracked locations'
                : 'No scoring room for it at your tracked locations'));
        } else if (where) {
            bits.push(field('Where', where));
        }
        if (b.level) bits.push(field('Level', b.level));
        if (b.overlapping) bits.push(field('Overlaps with', b.overlapping));
        if (b.notes) bits.push(field('Notes', b.notes));
        // The Easter Egg and Riddle answers. The source document hides these as
        // white-on-white text because each can only be solved once; this page
        // shows them outright, so opening a badge is enough to spend that.
        if (b.hint) bits.push(field('Hint', b.hint));
        if (b.giveaway) bits.push(field('Answer', b.giveaway));
        for (const t of b.tips || []) bits.push(field('Tip', t));
        for (const w of b.watch_out || []) bits.push(field('Watch out', w));
        for (const f of b.fun_facts || []) bits.push(field('Fun fact', f));

        // An empty panel has two different causes and they are not the same
        // news: a badge the documents cover only with a difficulty and a player
        // count has nothing *left* to show, since both are already in the row.
        // Only a badge they don't cover at all is a gap in the documents.
        const covered = b.difficulty || b.players || (b.rooms && b.rooms.length);
        const cell = el('td', { attrs: { colspan: String(cols) } }, [
            bits.length
                ? el('div', { className: 'badge-detail' }, bits)
                : el('div', { className: 'badge-detail' }, [
                    el('p', { className: 'muted small', text: covered
                        ? 'Nothing beyond the columns above — no room, tips or '
                          + 'notes recorded for this badge.'
                        : 'The community documents have no detail for this badge yet.' }),
                ]),
        ]);
        return el('tr', { className: 'detail-row' }, [cell]);
    }

    function field(label, value) {
        return el('p', { className: 'detail-line' }, [
            el('span', { className: 'detail-label', text: label }),
            el('span', { text: value }),
        ]);
    }


    function cell(st) {
        if (!st) return el('td', { className: 'muted', text: '—' });
        if (st.earned) {
            // The date is when we first *saw* it; a badge already held at the
            // player's first poll has none, and inventing one would be a lie.
            return el('td', { className: 'beat all' }, [
                el('span', { text: '✓', attrs: { 'aria-hidden': 'true' } }),
                el('span', { className: 'sr-only', text: 'earned' }),
                st.earned_on
                    ? el('span', { className: 'muted small', text: ' ' + st.earned_on })
                    : null,
            ]);
        }
        if (st.total_progress > 0 && st.progress > 0) {
            return el('td', { className: 'beat some', text: st.progress + '/' + st.total_progress });
        }
        if (st.progress > 0) {
            // No denominator — show the running count Activate reports, alone.
            return el('td', { className: 'beat some muted small', text: fmt.format(st.progress) });
        }
        return el('td', { className: 'beat none', text: '—' });
    }

    function renderStatus() {
        const players = activePlayers();
        const shown = visibleBadges().length;
        if (!players.length) {
            statusEl.textContent = 'No players selected.';
            return;
        }
        statusEl.textContent = shown + ' of ' + data.badges.length + ' badges shown for '
            + players.length + ' player' + (players.length === 1 ? '' : 's') + '.';
    }

    function fillSelect(sel, values) {
        const prev = sel.value;
        sel.replaceChildren(sel.options[0].cloneNode(true));   // keep the "All …" row
        for (const v of values) sel.appendChild(el('option', { text: v, attrs: { value: v } }));
        // Preserve the choice if the new list still has it, the way /games does.
        sel.value = [...sel.options].some(o => o.value === prev) ? prev : '';
    }

    function fillFacetFilters() {
        const rooms = new Set();
        for (const b of data.badges) for (const r of b.rooms || []) rooms.add(r);
        fillSelect(roomSel, [...rooms].sort());
        // Fixed list rather than what happens to be present, so the options
        // don't reshuffle as badges come and go.
        fillSelect(difficultySel, DIFFICULTIES);
    }

    function fillPlayerFilter() {
        playerBox.replaceChildren();
        for (const p of data.players) {
            const input = el('input', { attrs: { type: 'checkbox', value: String(p.id) } });
            input.checked = selectedPlayers.has(p.id);
            input.addEventListener('change', () => {
                if (input.checked) selectedPlayers.add(p.id);
                else selectedPlayers.delete(p.id);
                save('players', [...selectedPlayers]);
                render();
            });
            playerBox.appendChild(
                el('label', { className: 'player-check' }, [
                    input,
                    el('span', { text: p.display_name }),
                ])
            );
        }
    }

    // ---------- load ----------

    async function load_() {
        statusEl.textContent = 'Loading…';
        try {
            const resp = await fetch('/api/badge-data', { credentials: 'same-origin' });
            if (!resp.ok) throw new Error('badge-data ' + resp.status);
            data = await resp.json();
        } catch (e) {
            statusEl.textContent = 'Failed to load badge data.';
            closeCard.hidden = true;
            gridCard.hidden = true;
            return;
        }

        const known = new Set(data.players.map(p => p.id));
        const stored = load('players', null);
        selectedPlayers = new Set(
            stored === null ? [...known] : stored.filter(id => known.has(id))
        );
        if (!selectedPlayers.size) selectedPlayers = new Set(known);

        fillFacetFilters();
        fillPlayerFilter();

        // Restored after the fetch, since the room list is built from the
        // payload. Both selects silently ignore a value the data no longer has.
        for (const [sel, name] of [[stateSel, 'state'], [roomSel, 'room'],
                                   [difficultySel, 'difficulty']]) {
            const saved = load(name, '');
            if (saved && [...sel.options].some(o => o.value === saved)) sel.value = saved;
        }
        hereOnlyBox.checked = load('here', false) === true;

        const savedSort = load('sort', null);
        if (savedSort && (savedSort.dir === 1 || savedSort.dir === -1)) {
            // A sort by a player who is no longer tracked has no column to
            // point at, so it falls back to the API's order rather than
            // silently ranking everything equal.
            const stillThere = !String(savedSort.key).startsWith('p:')
                || known.has(Number(String(savedSort.key).slice(2)));
            if (stillThere) sort = { key: savedSort.key, dir: savedSort.dir };
        }

        render();
    }

    for (const [sel, name] of [[stateSel, 'state'], [roomSel, 'room'],
                               [difficultySel, 'difficulty']]) {
        sel.addEventListener('change', () => {
            save(name, sel.value);
            render();
        });
    }
    hereOnlyBox.addEventListener('change', () => {
        save('here', hereOnlyBox.checked);
        render();
    });
    searchInput.addEventListener('input', render);

    load_();
})();
