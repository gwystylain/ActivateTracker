// /games — per-level breakdown for one location, compared across players,
// plus the Point Farmer ranking.
//
// The API sends one location whole (catalog + every player's sparse beaten-set
// + the location's top scores), so every filter, expand and re-rank below is a
// re-read of data already in hand. Only changing the location refetches.
(function () {
    const locationSel = document.getElementById('locationFilter');
    if (!locationSel) return;

    const gameSel = document.getElementById('gameFilter');
    const modeSel = document.getElementById('modeFilter');
    const unbeatenBox = document.getElementById('unbeatenOnly');
    const playerBox = document.getElementById('playerFilter');
    const statusEl = document.getElementById('gamesStatus');
    const levelsCard = document.getElementById('levelsCard');
    const levelHead = document.getElementById('levelHead');
    const levelBody = document.getElementById('levelBody');
    const farmerCard = document.getElementById('farmerCard');
    const farmerBody = document.getElementById('farmerBody');
    const farmerNote = document.getElementById('farmerNote');
    const unscoredCard = document.getElementById('unscoredCard');
    const unscoredBody = document.getElementById('unscoredBody');
    const unscoredNote = document.getElementById('unscoredNote');
    const recordsCard = document.getElementById('recordsCard');
    const recordsBody = document.getElementById('recordsBody');
    const recordsNote = document.getElementById('recordsNote');

    const fmt = new Intl.NumberFormat();
    const KEY = 'atrk.games';
    const FARM_LIMIT = 12;

    let data = null;                     // last /api/game-data payload
    let selectedPlayers = new Set();     // player ids (numbers)
    const expanded = new Set();          // game_ids showing their level rows

    // Blocked storage throws on access; a saved filter is not worth taking the
    // page down for, so both directions swallow it (same as the chart's
    // atrk.chartMetric handling in app.js).
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
            // textContent throughout: game and player names are third-party
            // strings and must never be parsed as markup.
            if (opts.text !== undefined) node.textContent = opts.text;
            if (opts.attrs) {
                for (const [k, v] of Object.entries(opts.attrs)) node.setAttribute(k, v);
            }
        }
        for (const child of children || []) if (child) node.appendChild(child);
        return node;
    }

    // ---------- data helpers ----------

    function games() {
        // Flat [{room, game}] in the site's own room/gamemode order.
        const out = [];
        for (const room of data.rooms) {
            for (const game of room.games) out.push({ room, game });
        }
        return out;
    }

    function visibleGames() {
        const roomId = gameSel.value;
        const gameId = modeSel.value;
        return games().filter(({ room, game }) => {
            if (roomId && String(room.room_id) !== roomId) return false;
            if (gameId && String(game.game_id) !== gameId) return false;
            return true;
        });
    }

    function activePlayers() {
        return data.players.filter(p => selectedPlayers.has(p.id));
    }

    function scoreOf(playerId, gameId, levelId) {
        const byGame = data.scores[String(playerId)];
        if (!byGame) return null;
        const byLevel = byGame[String(gameId)];
        if (!byLevel) return null;
        const v = byLevel[String(levelId)];
        return v === undefined ? null : v;
    }

    function topScoreOf(gameId, levelId) {
        const byLevel = data.top_scores[String(gameId)];
        if (!byLevel) return null;
        const v = byLevel[String(levelId)];
        return v === undefined ? null : v;
    }

    // Top scores are sparse — a level nobody at this location has ever scored
    // has no entry at all. Left at zero those levels would rank *below* ones
    // people have already played, which is backwards for farming. Scores track
    // the level index closely across gamemodes (level 1 ≈ 2k, level 10 ≈ 10k),
    // so the median at the same index is a fair stand-in.
    let medianByLevel = null;

    function buildMedians() {
        const byLevel = new Map();
        for (const [gameId, levels] of Object.entries(data.top_scores)) {
            for (const [lvl, score] of Object.entries(levels)) {
                if (!byLevel.has(lvl)) byLevel.set(lvl, []);
                byLevel.get(lvl).push(score);
            }
        }
        medianByLevel = new Map();
        for (const [lvl, scores] of byLevel) {
            scores.sort((a, b) => a - b);
            medianByLevel.set(lvl, scores[Math.floor(scores.length / 2)]);
        }
    }

    function estimatedTopScore(gameId, levelId) {
        const known = topScoreOf(gameId, levelId);
        if (known !== null) return known;
        return medianByLevel.get(String(levelId)) || 0;
    }

    // Levels nobody at this location has ever scored. Top scores are sparse —
    // a missing key means no score — and a stored zero says the same thing, so
    // both count. Nothing to do with the player filter: this is the whole
    // venue's board, tracked players included only by being part of it.
    function unscoredIn(game) {
        return game.levels.filter(lvl => !topScoreOf(game.game_id, lvl));
    }

    // Does this player's best *hold* the location's top score? Equal counts —
    // the top score is only ever somebody's own number, so matching it means
    // holding it (shared with whoever else tied). Greater than counts too: the
    // room pages that supply top scores are re-read on a throttle, so a player
    // who has just beaten the board reads high until the next walk.
    function holdsTop(playerId, gameId, levelId) {
        const top = topScoreOf(gameId, levelId);
        if (!top) return false;   // nobody has scored it — no record to hold
        const mine = scoreOf(playerId, gameId, levelId);
        return mine !== null && mine >= top;
    }

    function recordsIn(playerId, game) {
        return game.levels.filter(lvl => holdsTop(playerId, game.game_id, lvl));
    }

    function beatenCount(playerId, game) {
        let n = 0;
        for (const lvl of game.levels) if (scoreOf(playerId, game.game_id, lvl) !== null) n++;
        return n;
    }

    // Unscored (player, level) pairs in a gamemode, and the top-score points
    // sitting behind them. This is exactly what Point Farmer ranks on.
    function openIn(game, players) {
        let open = 0;
        let points = 0;
        for (const lvl of game.levels) {
            for (const p of players) {
                if (scoreOf(p.id, game.game_id, lvl) === null) {
                    open++;
                    points += estimatedTopScore(game.game_id, lvl);
                }
            }
        }
        return { open, points, slots: game.levels.length * players.length };
    }

    // ---------- filter controls ----------

    function fillGameFilter() {
        const prev = gameSel.value;
        gameSel.replaceChildren(el('option', { text: 'All games', attrs: { value: '' } }));
        for (const room of data.rooms) {
            gameSel.appendChild(
                el('option', { text: room.name, attrs: { value: String(room.room_id) } })
            );
        }
        gameSel.value = [...gameSel.options].some(o => o.value === prev) ? prev : '';
    }

    function fillModeFilter() {
        const prev = modeSel.value;
        const roomId = gameSel.value;
        modeSel.replaceChildren(el('option', { text: 'All gamemodes', attrs: { value: '' } }));
        for (const { room, game } of games()) {
            if (roomId && String(room.room_id) !== roomId) continue;
            modeSel.appendChild(
                el('option', {
                    // Prefixed when showing every room's modes, since gamemode
                    // names repeat across rooms (Grid → "Grid", Mega Grid → "Mega Grid").
                    text: roomId ? game.name : room.name + ' → ' + game.name,
                    attrs: { value: String(game.game_id) },
                })
            );
        }
        modeSel.value = [...modeSel.options].some(o => o.value === prev) ? prev : '';
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

    // ---------- rendering ----------

    function render() {
        const players = activePlayers();
        renderFarmer(players);
        renderUnscored();
        renderRecords(players);
        renderLevels(players);
        renderStatus(players);
    }

    function renderStatus(players) {
        const cat = data.catalog;
        const bits = [];
        if (!cat) {
            bits.push('No game catalog for this location yet — it is built on the next refresh that sees a score change.');
        } else {
            bits.push(cat.catalog_levels + ' levels catalogued');
            if (cat.level_count && cat.level_count !== cat.catalog_levels) {
                bits.push('site reports ' + cat.level_count + ' — catalog is incomplete');
            }
            // Stored as an ISO instant with a +00:00 offset; trimming to
            // minutes drops the offset that "UTC" already says.
            if (cat.fetched_at) bits.push('catalog read ' + cat.fetched_at.slice(0, 16).replace('T', ' ') + ' UTC');
        }
        if (data.players.length && !players.length) bits.push('select a player to see scores');
        statusEl.textContent = bits.join(' · ');
    }

    function renderFarmer(players) {
        if (!players.length || !data.rooms.length) {
            farmerCard.hidden = true;
            return;
        }
        farmerCard.hidden = false;

        const ranked = visibleGames()
            .map(({ room, game }) => ({ room, game, ...openIn(game, players) }))
            .filter(r => r.open > 0)
            .sort((a, b) => b.open - a.open || b.points - a.points);

        farmerBody.replaceChildren();
        ranked.slice(0, FARM_LIMIT).forEach((r, i) => {
            farmerBody.appendChild(
                el('tr', null, [
                    el('td', { className: 'muted', text: String(i + 1) }),
                    el('td', { text: r.room.name }),
                    el('td', { text: r.game.name }),
                    el('td', null, [
                        el('strong', { text: String(r.open) }),
                        el('span', { className: 'muted small', text: ' of ' + r.slots }),
                    ]),
                    el('td', { text: r.points ? '~' + fmt.format(r.points) : '—' }),
                ])
            );
        });

        if (!ranked.length) {
            farmerNote.textContent =
                'Every selected player has a score on every level in view. Nothing left to farm here.';
            return;
        }
        const names = players.map(p => p.display_name).join(', ');
        farmerNote.textContent =
            'Open levels counts (player × level) pairs with no score, for ' + names +
            '. Points on the table sums this location’s top score for those levels, ' +
            'standing in the median score for that level number where nobody here has ' +
            'scored yet — a rough ceiling, not a prediction. Reflects the filters above.';
    }

    function renderUnscored() {
        const inView = visibleGames();
        if (!inView.length) {
            unscoredCard.hidden = true;
            return;
        }
        unscoredCard.hidden = false;

        // Emptiest gamemodes first; sort is stable, so equal counts keep the
        // site's own room/gamemode order.
        const rows = inView
            .map(({ room, game }) => ({ room, game, levels: unscoredIn(game) }))
            .filter(r => r.levels.length)
            .sort((a, b) => b.levels.length - a.levels.length);

        unscoredBody.replaceChildren();
        for (const r of rows) {
            const total = r.game.levels.length;
            unscoredBody.appendChild(
                el('tr', null, [
                    el('td', { text: r.room.name }),
                    el('td', { text: r.game.name }),
                    el('td', {
                        className: 'count' + (r.levels.length === total ? ' full' : ''),
                    }, [
                        el('strong', { text: String(r.levels.length) }),
                        el('span', { className: 'muted small', text: ' of ' + total }),
                    ]),
                    el('td', null, levelChips(r.levels, 'level-chip')),
                ])
            );
        }

        if (!rows.length) {
            unscoredNote.textContent =
                'Every level in view has a top score — nothing here is untouched.';
            return;
        }
        const levels = rows.reduce((sum, r) => sum + r.levels.length, 0);
        unscoredNote.textContent =
            levels + ' level' + (levels === 1 ? '' : 's') + ' across ' + rows.length +
            ' gamemode' + (rows.length === 1 ? '' : 's') + ', by the site’s level numbering. ' +
            'Follows the game and gamemode filters; the player filter does not apply. ' +
            'A location’s top scores are only re-read when one of the tracked players ' +
            'scores there, so a very recent first score may not have landed yet.';
    }

    // Level numbers as chips, in the site's 1-based numbering. Real text nodes
    // between them, not just margin, so the numbers stay separate when the cell
    // is read aloud or copied.
    function levelChips(levels, className) {
        return levels.flatMap((lvl, i) => [
            i ? document.createTextNode(' ') : null,
            el('span', { className: className, text: String(lvl + 1) }),
        ]);
    }

    function renderRecords(players) {
        const inView = visibleGames();
        if (!players.length || !inView.length) {
            recordsCard.hidden = true;
            return;
        }
        recordsCard.hidden = false;

        recordsBody.replaceChildren();
        // Grouped by player rather than ranked across them: the question this
        // answers is "what do I hold", one player at a time.
        const totals = [];
        for (const p of players) {
            const rows = inView
                .map(({ room, game }) => ({ room, game, levels: recordsIn(p.id, game) }))
                .filter(r => r.levels.length)
                .sort((a, b) => b.levels.length - a.levels.length);
            const held = rows.reduce((sum, r) => sum + r.levels.length, 0);
            totals.push({ name: p.display_name, held });
            if (!rows.length) continue;

            recordsBody.appendChild(
                el('tr', { className: 'group-row' }, [
                    el('th', {
                        text: p.display_name + ' — ' + held + (held === 1 ? ' record' : ' records'),
                        attrs: { colspan: '4', scope: 'colgroup' },
                    }),
                ])
            );
            for (const r of rows) {
                recordsBody.appendChild(
                    el('tr', null, [
                        el('td', { text: r.room.name }),
                        el('td', { text: r.game.name }),
                        el('td', { className: 'count' }, [
                            el('strong', { text: String(r.levels.length) }),
                            el('span', {
                                className: 'muted small',
                                text: ' of ' + r.game.levels.length,
                            }),
                        ]),
                        el('td', null, levelChips(r.levels, 'level-chip record')),
                    ])
                );
            }
        }

        if (!totals.some(t => t.held)) {
            recordsNote.textContent =
                'None of the selected players hold a top score in view. Levels nobody ' +
                'has scored at all are in Never scored above — those are the cheapest ' +
                'records to take.';
            return;
        }
        recordsNote.textContent =
            totals.map(t => t.name + ' ' + t.held).join(' · ') +
            '. A record means the player’s best equals this location’s top score — an ' +
            'exact tie counts, and so does a score that beat the board since the last ' +
            'room walk. Follows the game and gamemode filters.';
    }

    function renderLevels(players) {
        const rows = visibleGames();
        levelsCard.hidden = !rows.length;
        if (!rows.length) return;

        levelHead.replaceChildren(
            el('tr', null, [
                el('th', { text: 'Gamemode' }),
                el('th', { text: 'Top score' }),
                ...players.map(p => el('th', { text: p.display_name })),
            ])
        );

        levelBody.replaceChildren();
        let lastRoom = null;
        for (const { room, game } of rows) {
            if (unbeatenBox.checked && players.length && openIn(game, players).open === 0) {
                continue;
            }
            if (room.room_id !== lastRoom) {
                lastRoom = room.room_id;
                levelBody.appendChild(
                    el('tr', { className: 'room-row' }, [
                        el('th', {
                            text: room.name,
                            attrs: { colspan: String(2 + players.length), scope: 'colgroup' },
                        }),
                    ])
                );
            }
            levelBody.appendChild(gameRow(room, game, players));
            if (expanded.has(game.game_id)) {
                for (const lvl of game.levels) levelBody.appendChild(levelRow(game, lvl, players));
            }
        }
    }

    function gameRow(room, game, players) {
        const isOpen = expanded.has(game.game_id);
        const ceiling = game.levels.reduce((sum, lvl) => sum + (topScoreOf(game.game_id, lvl) || 0), 0);

        const row = el('tr', {
            className: 'game-row' + (isOpen ? ' expanded' : ''),
            attrs: { role: 'button', tabindex: '0', 'aria-expanded': isOpen ? 'true' : 'false' },
        }, [
            el('td', null, [
                el('span', { className: 'toggle', text: '▸', attrs: { 'aria-hidden': 'true' } }),
                el('span', { text: game.name }),
            ]),
            el('td', {
                className: 'muted small',
                text: ceiling ? fmt.format(ceiling) : '—',
                attrs: { title: 'Total of every level’s top score at this location' },
            }),
            ...players.map(p => {
                const n = beatenCount(p.id, game);
                const total = game.levels.length;
                const cls = n === 0 ? 'none' : (n === total ? 'all' : 'some');
                const held = recordsIn(p.id, game).length;
                // The crown count says the row is worth expanding — the levels
                // themselves are marked inside it.
                return el('td', { className: 'beat ' + cls }, [
                    el('span', { text: n + '/' + total }),
                    held
                        ? el('span', {
                            className: 'rec-badge',
                            text: '👑' + held,
                            attrs: { title: held + ' top score' + (held === 1 ? '' : 's') + ' held here' },
                        })
                        : null,
                ]);
            }),
        ]);

        function toggle() {
            if (expanded.has(game.game_id)) expanded.delete(game.game_id);
            else expanded.add(game.game_id);
            renderLevels(activePlayers());
        }
        row.addEventListener('click', toggle);
        row.addEventListener('keydown', e => {
            if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                toggle();
            }
        });
        return row;
    }

    function levelRow(game, levelId, players) {
        const top = topScoreOf(game.game_id, levelId);
        return el('tr', { className: 'level-row' }, [
            // Level ids are 0-based in the payload; the site numbers them from 1.
            el('td', { className: 'level-name', text: 'Level ' + (levelId + 1) }),
            el('td', { className: 'muted small', text: top === null ? '—' : fmt.format(top) }),
            ...players.map(p => {
                const score = scoreOf(p.id, game.game_id, levelId);
                if (score === null) return el('td', { className: 'no-score', text: 'No score' });
                if (!holdsTop(p.id, game.game_id, levelId)) {
                    return el('td', { text: fmt.format(score) });
                }
                // Colour alone can't carry this, so the crown and the
                // screen-reader text say it too.
                return el('td', {
                    className: 'top-score',
                    attrs: { title: 'Location top score' },
                }, [
                    el('span', { className: 'crown', text: '👑', attrs: { 'aria-hidden': 'true' } }),
                    el('span', { className: 'val', text: fmt.format(score) }),
                    el('span', { className: 'sr-only', text: ' — location top score' }),
                ]);
            }),
        ]);
    }

    // ---------- load ----------

    async function loadLocation(locationId) {
        statusEl.textContent = 'Loading…';
        try {
            const resp = await fetch('/api/game-data?location_id=' + encodeURIComponent(locationId), {
                credentials: 'same-origin',
            });
            if (!resp.ok) throw new Error('game-data ' + resp.status);
            data = await resp.json();
        } catch (e) {
            statusEl.textContent = 'Failed to load game data.';
            levelsCard.hidden = true;
            farmerCard.hidden = true;
            unscoredCard.hidden = true;
            recordsCard.hidden = true;
            return;
        }

        expanded.clear();
        buildMedians();
        const known = new Set(data.players.map(p => p.id));
        // Keep the selection across locations where it still applies; default
        // to everyone the first time.
        const stored = selectedPlayers.size ? [...selectedPlayers] : load('players', null);
        selectedPlayers = new Set(
            stored === null ? [...known] : stored.filter(id => known.has(id))
        );
        if (!selectedPlayers.size) selectedPlayers = new Set(known);

        fillGameFilter();
        fillModeFilter();
        fillPlayerFilter();
        render();
    }

    locationSel.addEventListener('change', () => {
        save('location', locationSel.value);
        loadLocation(locationSel.value);
    });
    gameSel.addEventListener('change', () => {
        save('game', gameSel.value);
        fillModeFilter();
        save('mode', modeSel.value);
        render();
    });
    modeSel.addEventListener('change', () => {
        save('mode', modeSel.value);
        render();
    });
    unbeatenBox.addEventListener('change', () => {
        save('unbeaten', unbeatenBox.checked);
        render();
    });

    const savedLocation = load('location', null);
    if (savedLocation !== null && [...locationSel.options].some(o => o.value === String(savedLocation))) {
        locationSel.value = String(savedLocation);
    }
    unbeatenBox.checked = load('unbeaten', false) === true;

    loadLocation(locationSel.value).then(() => {
        // Filters are populated from the payload, so restoring them has to wait
        // for the fetch. Both selects silently ignore values the location lacks.
        const g = load('game', '');
        if (g && [...gameSel.options].some(o => o.value === g)) {
            gameSel.value = g;
            fillModeFilter();
        }
        const m = load('mode', '');
        if (m && [...modeSel.options].some(o => o.value === m)) modeSel.value = m;
        if (g || m) render();
    });
})();
