"""Gamemode descriptions from the community-maintained Activate Master Document.

Static reference text, not scraped: the site's own pages carry no description
for a gamemode, so the /games tooltips come from here. Keyed by room because
names repeat across rooms and the rules need not be the same game: Mega Laser's
Defuse asks for 8 rounds where Trench's asks for "enough targets".

Only cooperative gamemodes are listed. Those are what `roomGames` exposes, since
the competitive games have no levels and no leaderboard. Where a room runs a
competitive game under the same name (Hoops' Barrage, Hide's Numbers), it is the
cooperative rules that are written down here.

The document covers every room Activate has built; a location only ever has some
of them, and a gamemode with no entry here simply renders without a tooltip.
"""
from __future__ import annotations

# {room name: {gamemode name: description}} — both keys exactly as the site
# spells them in location.rooms[].name / roomGames[].name.
DESCRIPTIONS: dict[str, dict[str, str]] = {
    "Arena": {
        "Digby": (
            "Recreate the pattern shown on the targets for a short period of time by "
            "throwing balls at the specific targets that lit up."
        ),
        "Hunt": (
            "Hit the targets on the wall that match the one shown on the top targets. Avoid "
            "red targets or you will lose a life."
        ),
        "Memory": (
            "Hit white targets on the wall to reveal the underlying pattern. Match two to "
            "make them disappear, and match every pair to win."
        ),
        "Swat": (
            "Quickly hit the appearing blue targets on the wall to score, and avoid reds or "
            "you will lose a life. Speed is key or the blue targets will disappear before "
            "you hit them."
        ),
    },
    "Climb": {
        "Technique": (
            "Hit all the blues while avoiding the reds, crossing the wall a total of 3 "
            "times. Avoid reds or you will lose a life."
        ),
        "Whack-A-Hold": (
            "Blue rocks will pop up in the field of green you are given. Grab the blue "
            "rocks before they disappear, but make sure to avoid red ones that move in a "
            "pattern."
        ),
    },
    "Control": {
        "Bop": (
            "Use the D-Pad, buttons, and floor tiles to complete four different types of "
            "quick-fire minigames. Complete enough rounds of minigames without being too "
            "slow to win."
        ),
        "Labyrinth": (
            "Use the D-Pad to maneuver your pixel through a maze. Avoid red pixels while "
            "collecting blues scattered through the maze. Run in place on the floor tiles "
            "to move your pixel faster."
        ),
        "Runner": (
            "Use the footpads to move your dot up the screen and reach the end while "
            "avoiding moving reds. Hit a red, and you’re sent back to the start."
        ),
        "Bumper": (
            "Slide your coloured tank along the side walls and ceiling using the D-Pad. "
            "Jump to shoot bullets that will clear blocks of your colour. Clear all "
            "coloured blocks while avoiding reds to win."
        ),
        "Collect": (
            "Move side to side using the arrows to go through the same-coloured blocks to "
            "gain points, while avoiding the red blocks."
        ),
    },
    "Grid": {
        "Flash Mines": (
            "Start the game by standing on the green tiles. Memorize where all the coloured "
            "tiles are, and step on the ones that are called out by the announcer when they "
            "disappear. Avoid red tiles. Once you have stepped on all tiles needed, move to "
            "the green to advance the wave."
        ),
        "Grid": (
            "Start the game on the green tiles. Collect all blue tiles by stepping on them, "
            "then move to the green tiles to advance to the next wave. Avoid reds or you "
            "will lose a life."
        ),
        "Strategy": (
            "Step on an orange tile to shoot it forward. An orange tile will clear a blue "
            "tile when it hits one. Clear all blue tiles to move to the next wave. Don’t "
            "hit red tiles or you will lose a life."
        ),
        "Zones": (
            "Listen to the colour called out by the announcer. Move quickly to that colour "
            "zone on the ground before time runs out. If not all zones are triggered in "
            "time, you will lose a life."
        ),
        "Tile Relay": (
            "Start the game on a coloured square on the wall. When your colour tiles appear "
            "in the middle, run out and collect them while avoiding reds. Return to your "
            "square once you have collected them all to pass the turn to the next player."
        ),
    },
    "Grip": {
        "Globe": (
            "Match groups of 3 images for a certain number of countries. The 3 images will "
            "be 1) the silhouette of the country, 2) the flag of the country, and 3) a "
            "landmark from the country. Grab the 3 corresponding handholds for a specific "
            "country (the order doesn’t matter). If correct, you’ll hear a ding and those 3 "
            "displays will turn off. If incorrect, they’ll stay and you’ll lose a life."
        ),
        "Loop": (
            "Choose and remember a sequence of fruits & vegetables, then each player goes "
            "across the wall by following that sequence in order. Each of the three "
            "handholds in the first column will each show one fruit. Grab one handhold to "
            "pick which of the three you want to be first in your sequence. Then it will "
            "show another three. Again grab one, choosing it to be your second in your "
            "sequence. Repeat until you have selected the full sequence. When you go "
            "across, each column will display three fruits: one that is the next one in "
            "your sequence, and two fakes. (Ex: you pick pear, peach, apple, orange. Since "
            "the wall is 24 holds long, you will go across grabbing pear in column 1 (ex: "
            "from banana, pear, lime), peach in column 2 (ex: from peach, pear, mushroom), "
            "… pear in column 5, … apple in column 23, and orange in column 24)"
        ),
        "Traverse": (
            "Hit all the blues while avoiding the reds, crossing the wall a total of 3 "
            "times. Avoid reds or you will lose a life."
        ),
        "Firewall": "Hit the numbers in ascending order while avoiding the moving fire.",
    },
    "Hide": {
        "Numbers": (
            "Panels in the room will display numbers, which can be triggered by hitting the "
            "button underneath the corresponding panel. Hit all the numbers in ascending "
            "order, before time runs out to win. When one eye starts flashing, that means "
            "it will eventually turn red. When it turns red, if the eye sees you then "
            "you’ll lose a life."
        ),
        "Relay": (
            "Everyone starts by holding a coloured button. When your colour disappears from "
            "under your hand, find it in the room somewhere else and run to it. Press it "
            "before you run out of time, and hold it until it disappears again."
        ),
        "Sequence": (
            "Hold down a blue button to reveal the underlying number. Memorize all the "
            "numbers, then count in order from 1 to the highest number."
        ),
        "Spellinator": (
            "A random selection of letters will appear on the walls. With these letters, "
            "spell any word of the length indicated on the scoreboard."
        ),
        "Words": (
            "Read the word on the screen and spell it out by hitting the letters in the "
            "correct order. Spell enough words to win."
        ),
    },
    "Hoops": {
        "15 Green": (
            "Score baskets on the green hoops 15 times to move to the next wave. Beat 3 "
            "waves to win. Avoid red baskets. Black baskets are safe, but you don’t get any "
            "credit."
        ),
        "Barrage": (
            "Sink the required number of baskets in the lit up hoops before time runs out."
        ),
        "Simon Says": (
            "Recreate the sequence of colours shown at the start of the round in order by "
            "shooting the correct hoop."
        ),
        "Trivial": (
            "Shoot the basket corresponding to the correct answer to the question on the "
            "screen."
        ),
    },
    "Laser": {
        "Chopper": (
            "Stay alive by dodging the lasers coming at you from the left, right, and "
            "above. Avoid a certain number of waves of lasers to win."
        ),
        "Sneak": (
            "Like a classic spy movie, run, crawl, and jump through the laser maze without "
            "hitting the lasers. Once everyone is at the other side, hold the button to "
            "advance to the next wave."
        ),
        "Laser Maze": (
            "Like a classic spy movie, run, crawl, and jump through the laser maze without "
            "hitting the lasers. Once everyone is at the other side, hold the button to "
            "advance to the next wave."
        ),
        "Photon Rush": (
            "Dodge enough lasers coming at you to win. Press the button behind you to send "
            "more lasers. If you can dodge them, you’ll complete the level faster and earn "
            "a better score. Pressing the button is needed to beat the level in time for "
            "higher levels."
        ),
    },
    "Mega Grid": {
        "Gridlock": (
            "Step on an orange tile to shoot it straight forward. Clear all blue tiles to "
            "advance to the next wave, and avoid red tiles or you will lose a life."
        ),
        "Jigsaw": (
            "Look at the patterns in the middle of the room. Find the matching pieces "
            "outside of the middle and step on them. Move back to the green once all pieces "
            "are clear to advance to the next wave."
        ),
        "Mega Grid": (
            "Start the game on the green tiles. Step on blue tiles to collect them while "
            "avoiding reds. Press the blue buttons to collect them as well. Once you have "
            "collected all blue tiles, move to the green tiles to advance to the next wave."
        ),
        "Mega Relay": (
            "Everyone must hold a coloured button at the start of the game. When your "
            "colour disappears, find it elsewhere in the room and run to it while dodging "
            "the pattern of reds. Hold your button and wait for it to disappear again, and "
            "repeat."
        ),
        "Mega Zones": (
            "The announcer in the room will call out a colour. Everyone must find that "
            "colour zone on the floor and run to it before time runs out. Clear enough "
            "zones to win."
        ),
        "Order Up": (
            "Press the buttons on the wall starting at 1 and count to the highest number "
            "displayed. Avoid red tiles. Green tiles are safe."
        ),
        "Sharpshooter": (
            "Orange tiles will appear in the room, as well as moving blues and reds. Step "
            "on an orange tile to shoot it straight forward, and clear all blues to advance "
            "to the next wave. Avoid red tiles or you will lose a life."
        ),
        "Statues": (
            "Coloured timers will be counting down on the wall. When they hit zero, "
            "everyone in the room must stay completely still to avoid losing lives. During "
            "the time when they are counting down, step on as many coloured tiles as you "
            "can that match the colour of the timers. Step on enough correct tiles to win."
        ),
    },
    "Mega Laser": {
        "Defuse": (
            "Defuse the moving white targets by filling them up green before they explode. "
            "Avoid red targets, and survive 8 rounds to win."
        ),
        "Gauntlet": (
            "Stay alive by dodging the lasers coming at you from the left and right. Avoid "
            "a certain number of waves of lasers to win."
        ),
        "Laser Relay": (
            "Everyone holds a unique coloured button. When your coloured laser appears in "
            "the middle of the maze, go across, avoiding the lasers except your coloured "
            "laser, which you must trigger. Once across, hold your coloured button until it "
            "is your turn again."
        ),
        "Maze": (
            "Like a classic spy movie, run, crawl, and jump through the laser maze without "
            "hitting them. Once at the other side, every team member must hold their "
            "buttons to advance to the next wave."
        ),
        "Zap": (
            "Shoot the blue targets while avoiding hitting the red targets. Shoot enough "
            "blues in the time limit to win."
        ),
    },
    "Pipes": {
        "Clockstoppers": (
            "Put the ball through the top pipe to stop its timer (seen as a circle filling "
            "up). Stop the timer for all of the pipes to win."
        ),
        "Odd One Out": (
            "Find the one pipe in the room that doesn’t match any of the other pipes. "
            "Sometimes the difference is rotation speed, or only one has no pair in the "
            "room, or one has a unique colour no other has, etc."
        ),
        "Piperooni": (
            "Put the ball through the green top pipe, then, look at the colour(s) of the "
            "corresponding bottom pipe. Find that exact colour(s) in that orientation on a "
            "top pipe somewhere in the room, and put the ball through that pipe. Repeat "
            "until you win."
        ),
        "Scramble": (
            "Unscramble the letters on screen to make a word, then put the ball in the "
            "pipes in the correct order to spell the word for each wave."
        ),
    },
    "Portals": {
        "Jumble": (
            "Unscramble the letters on screen to make a word, then put the ball in the "
            "pipes in the correct order to spell the word for each wave."
        ),
        "Oddball": (
            "Find the one portal in the room that doesn’t match any of the other portals. "
            "Sometimes the difference is rotation speed, or only one has no pair in the "
            "room, or one has a unique colour no other has, etc."
        ),
        "Stopwatch": (
            "Put the ball through the top portal to stop its timer (seen as a circle "
            "filling up). Stop the timer for all of the portals to win."
        ),
        "Wormholes": (
            "Put the ball through the green top portal, then, look at the colour(s) of the "
            "corresponding bottom portal. Find that exact colour(s) in that orientation on "
            "a top portal somewhere in the room, and put the ball through that portal. "
            "Repeat until you win."
        ),
    },
    "Press": {
        "Bullet Train": (
            "Find a button on the inside of the train tracks (the second-lowest row of "
            "buttons). Find its exact match somewhere on the outside of the train tracks. "
            "Hit its outside match when the inside match is between the trains (two moving "
            "green lines)."
        ),
        "Gems": (
            "Look at the four buttons at the top middle of the room. Then, find the exact "
            "match on the wall for each lit up button. Do this for all 3 waves to win."
        ),
        "Link": (
            "Find, remember, and match the pairs shown around the room. Once you hit the "
            "first button of a match, the remaining ones disappear and turn white in "
            "colour. Continue matching as many as you can. Once you hit an incorrect pair, "
            "the white matches return back to colour so you can review and repeat step one."
        ),
        "Mines": (
            "Place bombs by pressing a black button, and detonate them by pressing the "
            "green button. When detonated, bombs will shoot in all 8 directions until "
            "hitting any coloured button other than the green one. Clear all white/blue "
            "buttons with the bombs you have (as shown on the screen) to win."
        ),
        "Undercover": (
            "Find the pattern shown in the middle hidden in one of the six boards around "
            "the room."
        ),
    },
    "Push": {
        "Blast": (
            "Place bombs by pressing a black button, and detonate them by pressing the "
            "green button. When detonated, bombs will shoot in all 8 directions until "
            "hitting a blue button or the edge of the board. Clear all blue buttons with "
            "the bombs you are given to win."
        ),
        "Camo": "Find the pattern in the middle of the room hidden on one of the six panels.",
        "Match": (
            "Find, remember, and match the pairs shown around the room. Once you hit the "
            "first button of a match, the remaining ones disappear and turn white in "
            "colour. Continue matching as many as you can. Once you hit an incorrect pair, "
            "the white matches return back to colour so you can review and repeat step one."
        ),
        "Press-cision": (
            "Look at the rectangle in the middle of the room. Find a button which has a "
            "pattern that isn’t plain white. Then, find its match somewhere else in the "
            "room. When the black button is over top of it, press it to clear it. Once you "
            "clear every button, you win."
        ),
        "Rings": (
            "Look at the four buttons at the top of the room. Then, find the exact match on "
            "the wall for each lit up button. Do this for all 3 waves to win."
        ),
    },
    "Scan": {
        "Inside Out": (
            "Start the game by selecting your colour. Memorize the colour patterns on the "
            "rings outside the screens, then recreate them by scanning your wristbands in "
            "order."
        ),
        "Spot": (
            "Spot the difference. Each screen has one element that isn’t on any other "
            "screen. Touch it, and if a green circle appears, you are right. Correctly "
            "touch the unique element on each screen to win."
        ),
        "Supermarket": (
            "Start the game by selecting your colour. Then, turn around and look at the "
            "middle screen (the “checkout”). There will be different items for each player "
            "indicated with their colour. Find those items (uncoloured) in one of the "
            "surrounding screens (the “aisles”). Select it on the screen, and then scan "
            "your wristband to pick up the item. Scan your wristband at the checkout after "
            "picking them up in the aisles to deliver them. Do this as many times as "
            "needed."
        ),
    },
    "Strike": {
        "Asteroids": "Destroy all the asteroids before they hit your ship!",
        "Dartmouth": (
            "Recreate the pattern shown on the screens for a short period of time by "
            "throwing balls at the specific targets that lit up."
        ),
        "Flip": (
            "Hit screens on the wall to reveal the underlying icon. Match two to make them "
            "disappear, and match every pair to win."
        ),
        "Seeker": (
            "Hit the screens on the wall that match the one displayed at the start of the "
            "game (which is also projected on the side wall). Avoid red targets or you will "
            "lose a life."
        ),
        "Terminal": (
            "Hit the screens in ascending order. Hitting a wrong screen will cause "
            "surrounding ones to shuffle around."
        ),
    },
    "Trench": {
        "Defuse": (
            "Defuse the moving white targets by filling them up green before they explode. "
            "Avoid red targets, and defuse enough targets to win."
        ),
        "Flash Fire": (
            "Memorize the colours of the targets overhead. The announcer will call out a "
            "colour of target to shoot at. Hit all of these coloured targets to advance to "
            "the next wave."
        ),
        "Trench": (
            "Shoot the glowing targets overhead, and avoid red targets. Once you clear all "
            "the glowing targets, crawl through the laser maze to the other side of the "
            "room. Then repeat and complete enough cycles to win."
        ),
        "Zap": (
            "Shoot the blue targets while avoiding hitting the red targets. Shoot enough "
            "blues in the time limit to win."
        ),
    },
}

# Names that mean the same thing whichever room they turn up in. Used only when
# the room name doesn't match — a location that spells a room differently still
# gets the description, while a name the document gives two different sets of
# rules for (only Defuse today) falls through to no tooltip rather than to the
# wrong room's game.
_BY_NAME_ONLY: dict[str, str] = {}
for _room in DESCRIPTIONS.values():
    for _name, _desc in _room.items():
        _BY_NAME_ONLY[_name] = _desc if _BY_NAME_ONLY.get(_name, _desc) == _desc else ""
_BY_NAME_ONLY = {k: v for k, v in _BY_NAME_ONLY.items() if v}


def describe(room_name: str | None, game_name: str | None) -> str | None:
    """Description for one gamemode, or None when the document doesn't cover it."""
    if not game_name:
        return None
    room = DESCRIPTIONS.get((room_name or "").strip())
    if room is not None:
        hit = room.get(game_name.strip())
        if hit:
            return hit
    return _BY_NAME_ONLY.get(game_name.strip())
