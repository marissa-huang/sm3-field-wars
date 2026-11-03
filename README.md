# SM3 Field Wars

## Start it

**Mac:** double-click `start.command`. If macOS blocks it, right-click it, choose Open, then Open again.
Or, from a terminal in this folder:

```bash
python3 server.py
```

The terminal prints three links using the laptop's Wi-Fi address, for example:

```
Projector  http://10.31.4.112:8000/display
Organizer  http://10.31.4.112:8000/admin
Hosts      http://10.31.4.112:8000/entry
PIN for organizer + hosts: tdsm3
```

**Change the PIN before the event:**

```bash
PIN=yourpin python3 server.py
```

Options: `PORT=8080` picks a different port, and `DATA_FILE=/path/state.json` picks where data is saved.

The default PIN is `tdsm3`. Any device that saved an older PIN will simply ask again.

Leave the terminal window open while the event runs. Ctrl+C stops the server. Everything is saved to
`data/state.json` after every change, so if the laptop restarts, start the server again and you're
back where you were.

## The four screens

| Page | Purpose |
|---|---|
| `/display` | Projector laptop, full-screen (press **F**) |
| `/admin` | Lead organizer, host the tournament and all of the rounds |
| `/entry` | For hosts to type in participant answer sheets |
| `/sheet` | Answer sheets to print beforehand |

### Network notes (important)

- Every device must reach the server laptop. Easiest is everyone on the **same Wi-Fi**.
- Some campus/guest Wi-Fi networks block device-to-device traffic. **Test in the actual room
  beforehand.** If phones can't load the link, use a **phone hotspot** that the server laptop
  and the host phones all join. That works reliably and needs very little data.
- The projector can simply be the server laptop itself, opening `http://localhost:8000/display`
  on an external display. Then the organizer uses a second laptop or a phone.

### Hosting it online instead (optional)

If you'd rather not depend on room Wi-Fi, deploy the folder to any host that runs Python
(for example Render, Railway, Fly.io, or a Stanford VM). The start command is `python3 server.py`.
Set `PORT` if the host provides one and set `PIN` as a secret. Keep it to **one instance**, because state
lives in memory and one JSON file. On hosts with a temporary disk, download a JSON backup between rounds.

## Scoring rules as implemented

- Every submitted team plays every other submitted team.
- **Field:** the team with more soldiers on each field wins the field.
- **Classic:** each field is worth 1. **Weighted:** Field *n* is worth *n*.
  A matchup is won by the team with more field points (equal field points is a tie).
- **Capital:** Each team names a capital. If your opponent puts **more** soldiers
  on your capital, you lose that matchup regardless of the other fields. If **both** capitals fall, it's a tie.
- **Matchup points:** win +10, tie +5, loss 0.
- **Ranking:** by points. Equal points share a rank (1, 1, 3…).

**Event cards** stack with any mode, and multiple cards can be combined.

| Card | Effect |
|---|---|
| 📦 Supply Shortage | Participants are only able to distribute 40 soldiers (editable) |
| 💎 Hidden Treasure | A random field is worth ×2 |
| ⛰️ Unstable Terrain | The field with the most total soldiers (all teams) is worth ×2. |
| 🌋 Volcanic Eruption | A random field is worth 0 points.|
| 🧊 Frozen Fortress | Field 3 worth 0 points. **One** neighbour (Field 2 or 4, drawn at random) is worth ×2 |
| 🐭 Underdog Victory | Capturing a field with 10 or fewer soldiers earns +2 extra field points in that matchup, for every such field |
| 💣 Sabotage | Each team also picks a field to sabotage. Against that team, every opponent has up to 10 fewer soldiers there. |
| 🏹 Siege Warfare | You need a lead of 5 or more soldiers to win a field. |
| 🌀 Random Teleportation | When a team's entry is saved, two random fields of its allocation are swapped. |
| 🎯 Exact Victory | Winning a field by exactly 1 soldier earns +1 extra field point |
| 🚫 No Man's Land | Exactly one field must be 0; the other four need at least 1 soldier |
| 🪜 Staircase | Field 1 ≤ Field 2 ≤ Field 3 ≤ Field 4 ≤ Field 5 |

Hidden Treasure, Volcano, and the Frozen Fortress doubled field are drawn when the round opens. The organizer
can see them (and **re-draw**) on the Live tab, and teleport swaps appear in the Live tab's **Swap** column.

**Default schedule (3 min each):**

1. Classic
2. Weighted
3. Capital
4. Capital + Unstable Terrain
5. Weighted + Volcanic Eruption
6. Classic + Hidden Treasure
7. Capital + Sabotage
8. Classic + Underdog Victory
9. Weighted + Supply Shortage
10. Classic + Frozen Fortress
11. Weighted + Siege Warfare
12. Classic + Random Teleportation
13. Capital + Exact Victory
14. Classic + No Man's Land
15. Capital + Staircase
