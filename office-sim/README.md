# Agent Office — top-down 3D office simulation

A standalone prototype that shows the agent society as people working inside an
office building, viewed from above. Where the 2D dashboard shows the message
stream and the kanban board, this view shows the same run as spatial behaviour:
who walked to the drawing board, who gathered round to listen, who went back to
their desk, and who is idle enough to be making coffee.

It is deliberately built as a separate page with its own scripted data, so the
look and the choreography can be settled before any of it is wired to the real
orchestrator event stream.

## Running it

It needs to be served over HTTP (ES modules will not load from `file://`):

```
python3 -m http.server 8731 --directory office-sim
```

then open <http://localhost:8731/>.

Three.js is vendored in `vendor/` rather than pulled from a CDN, so the
prototype runs offline and renders identically whenever it is revisited.

### View controls

| Input | Does |
|---|---|
| drag | pan |
| scroll | zoom |
| right-drag | rotate |
| `R` | reset the view |
| `G` | toggle station anchor markers |
| `L` | toggle name tags |
| `N` | toggle the nav grid, every cell an agent may not stand in |
| `P` | toggle the route each walking agent is following |
| `B` | toggle the activity badges |
| `1`–`6` | play a scenario from the panel, in the order listed there |
| `space` | play / pause |
| `enter` | restart the current scenario |
| `-` / `=` | slower / faster |
| `click` | inspect an agent; click empty floor to deselect |
| `esc` | close the inspector |
| drag the progress bar | seek to any point in the scenario |
| `←` / `→` | with the bar focused, seek 5s (hold shift for 1s); `home` / `end` jump to the ends |
| drag the panel handles | resize the scenario and log panels, or the rail's width; double-click a handle to reset |

The same controls are on screen in the Scenarios panel, which is the primary
way in; the keys are a convenience.

### URL parameters

| Parameter | Does |
|---|---|
| `?anchors=1` | Show station anchor markers at load, the quickest way to check every station still lands on the chair or prop it belongs to |
| `?labels=0` | Hide the name tags |
| `?focus=x,z` | Centre the view on a point on the floor, e.g. `?focus=-8,2.6` |
| `?zoom=n` | Zoom factor, roughly 0.55 to 4.5 |
| `?nav=1` | Show the nav grid. The fastest check that obstacles were derived correctly: a desk that failed to register shows as a hole, an over-inflated wall as a doorway sealed shut |
| `?paths=1` | Draw each walking agent's route in its own tier colour |
| `?goto=...` | Send agents somewhere on load. `?goto=board` and `?goto=home` move everyone; `?goto=cto-1:break_table;product-1:coffee` addresses them individually as `agentId:station` |
| `?still=1` | Draw a single frame instead of running the loop, and expose it for capture (see below) |
| `?t=n` | Wind the simulation forward `n` seconds before drawing. Only meaningful with `?still=1`, and the only way to capture a walk part-way through a stride |
| `?badges=0` | Hide the activity badges |
| `?scenario=id` | Play one on load: `scoping`, `retry`, `timeout`, `escalation`, `deferred`, `idle`. `?demo=` is accepted as an alias, which is what Layer 4 called it before the library existed |
| `?speed=n` | Playback speed: 0.5, 1, 2 or 4 |
| `?paused=1` | Load a scenario but hold it at the first frame |
| `?restartAt=n` | Press Restart `n` seconds in, so the reset path can be checked headlessly |
| `?select=id` | Open the inspector on one agent, e.g. `?select=engineering-2` |
| `?seed=n` | Seed for the office's randomness. The same seed replays the same run; the default is a fixed one |
| `?seekTo=n` | Scrub to `n` seconds on load. The bar needs a pointer, so this is how seeking gets checked headlessly |
| `?ticket=`, `?assignee=` | The ticket id and which engineering instance holds it (default `FYP-42`, `engineering-2`) |

### Capturing a frame

```
tools/capture.sh out.png                      # the whole office
tools/capture.sh pod.png "focus=-8,2.6&zoom=3.6"   # one corner, close up
```

This renders headlessly and is how every layer gets checked. Two details in
that script are load-bearing and should not be "simplified" away. It passes its
own `--user-data-dir`, because without one Chromium attaches to an already
running browser instead of starting a headless instance and the capture hangs.
And it takes the image from the page's own `canvas.toDataURL()` rather than
Chromium's `--screenshot`, because a one-shot WebGL render is not reliably still
in the compositor when `--screenshot` fires, which silently yields a blank
frame that looks exactly like a scene that failed to build.

## Build layers

The prototype is built in layers, each one self-contained and verifiable before
the next starts.

| Layer | Adds | State |
|---|---|---|
| 1 | Static office shell — floor plan, low walls, furniture, zones, camera | done |
| 2 | The five agents as people, colour-coded by tier, at their home stations | done |
| 3 | Navigation — nav grid, path following, walk animation, sit/stand | done |
| 4 | Behaviour state machine, idle quirks, and the failure paths | done |
| 5 | Scenario library with playback controls | done |
| 6 | Observation — click an agent for role and current action, event log | done |
| 7 | Replace the scripted timeline with the live orchestrator websocket stream | deferred |

## Files

| File | Holds |
|---|---|
| `js/layout.js` | The floor plan as data: walls, zones, furniture, doors, station anchors. Nothing else hardcodes a coordinate. |
| `js/characters.js` | The agent rig and its poses. Joints, not a welded mesh, so a figure can sit down and later walk. |
| `js/agents.js` | The roster and placing it at stations. Identities match the orchestrator's real agent ids. |
| `js/build.js` | Geometry builders that turn the layout into meshes. All primitives, no external assets. |
| `js/palette.js` | Every colour in one place, including the role-tier colours shared with the 2D dashboard. |
| `js/inspect.js` | Picking an agent out of the scene, the selection rings, and the inspector panel. |
| `js/eventlog.js` | Reads the events the office already emits and renders them as a log. |
| `js/rng.js` | Seeded random numbers, so a run can be replayed exactly. |
| `js/panels.js` | Dragging the HUD panels to resize them, and remembering the sizes. |
| `js/scenarios.js` | The scenario library, as data: each one a timeline of beats with captions. |
| `js/player.js` | Playback: which scenario is loaded, where it has got to, and the transport over it. |
| `js/tickets.js` | The ticket state machine, ported from the orchestrator's own `fsm.py`. |
| `js/behaviour.js` | What each agent is doing and why: the ticket-driven states, the idle quirks, and the failure paths. |
| `js/nav.js` | The walkable area, derived from the floor plan and the built geometry. A* over it, plus the debug overlays. |
| `js/locomotion.js` | Moving one agent to a named station: the rise/walk/turn/sit state machine, and the stride. |
| `js/scene.js` | Renderer, orthographic camera, lighting, camera controls, frame loop. |
| `js/main.js` | Wiring only. |

## Choices worth knowing about

**The camera is tilted, not straight down.** A literal 90-degree top-down view
of 3D people shows nothing but the tops of their heads. The camera sits about 35
degrees off vertical instead, which is what the Sims-style games actually do,
and it can be rotated within a band that never reaches eye level.

**The agents are rigged, not solid.** Every home station in the floor plan is a
seated one, so a figure that could not bend at the hip and knee would stand
inside its own chair. Poses are expressed as joint rotations, which is also what
the walk cycle needs, so the same rig carries Layer 3 without rework. The sign
convention catches people out: a limb is modelled pointing straight down, so a
positive X rotation swings it toward -Z, the direction the figure faces.

**Agent identities are the real ones.** `config/roles.yaml` declares cto (tier
3), product (tier 2) and engineering (tier 1, count 3), and the orchestrator
names the instances `<roleId>-<n>`. The office uses those same ids, so a message
from `engineering-2` in a real run already names the figure to animate, with no
mapping invented in between. Shirt colour is the role tier, matching the 2D
dashboard; everything else about a figure varies only so five people do not read
as five copies of one person.

**The walls are 1.35m, not full height.** Low walls mean the camera never needs
a cutaway system to see inside rooms. The side effect is that a wall hides
roughly a metre of floor immediately behind it at this pitch, so floor labels
and low furniture are kept clear of that band.

**The walkable area is derived, not drawn.** A hand-placed waypoint graph would
be a second set of coordinates to keep in step with `layout.js`, and it would rot
silently the first time a desk moved. Instead a 0.25m grid is laid over the floor
and blocked from two sources: the wall segments in `LAYOUT`, and the bounding
boxes of the furniture Layer 1 actually built, filtered to things tall enough to
walk into — which is why an agent crosses the rug but goes around a pot plant.
Doorways need no special case at all, because a door gap is an absent wall
segment, so its cells are simply never marked. Obstacles are inflated by the
agent's own radius, so a route that clears the grid clears it for a body with
width and the walk needs no separate collision pass. Run with `?nav=1` to see it.

**Both ends of a route are carved clear before searching.** Every home station is
a seated one, which means an agent begins and ends each trip standing inside a
chair's own inflated footprint. Without a carve-out, every desk in the office
would be unreachable from itself.

**Seeking replays rather than rewinds.** A simulation cannot be run backwards:
an agent half way across the office cannot be un-walked, and a merged ticket
cannot be un-merged. So dragging the progress bar resets the office and re-runs
the scenario at a fixed step up to the point asked for. That is only honest
because of the seeding below — against `Math.random` the same seek would land
somewhere slightly different every time, and scrubbing back and forth would
quietly change the run underneath you. A seek to `n` lands in exactly the state
that playing through to `n` reaches, which is checkable with `?seekTo=n` against
`?t=n`. A drag queues at most one seek per frame, so crossing the whole bar
replays once per paint rather than once per pointer event.

**The randomness is seeded.** Which break an idle agent wanders off to, how
long it stays, how a dropped document lands — all of it comes from a seeded
stream that is rewound whenever a scenario is loaded or restarted. Five agents
still need to behave like five people rather than one, but almost everything
here is checked by rendering a frame at a chosen moment and reading it, and two
captures of the same scenario have to be the same picture or a screenshot
proves nothing. `?seed=` picks a different run.

**Selection is a ring on the floor, not a recoloured figure.** The shirt colour
is the role tier and is the one thing a figure already tells you at a glance, so
tinting it to show selection would overwrite the information the selection is
meant to help you read. The ring takes the agent's own tier colour, and hover
uses a thinner neutral one.

**The event log is a reader, not a second source of truth.** `behaviour.js`
already emits an event on every ticket transition, timeout, document drop and
reset, so the panel subscribes rather than being told separately by whatever
caused the change. Layer 7 swaps the scripted driver for the live orchestrator
stream and the log keeps working, because it is watching the same events either
way. Only activity changes that say something you could not guess from watching
get a line; the rest would bury the transitions in noise.

**Picking is the one thing a screenshot cannot check**, since it needs a
pointer. `?select=` opens the inspector directly, and the diagnostics in a
`?still=1` capture include a self test that projects each agent back to screen
coordinates and picks there, reporting what it hit. Two agents standing on top
of each other can legitimately shadow one another, so a mismatch there is worth
reading rather than treating as an automatic failure.

**The scenario library is a standing feature, not scaffolding.** It does not go
away when Layer 7 brings in live data. A real run is slow, costs real money, and
cannot be made to fail on demand; the library plays any of the interesting cases
in under a minute, including the ones a live run would only reach by accident.
Scenarios are data rather than code — a time, an action from a fixed vocabulary,
and a caption — so one can be read end to end without following function calls,
and so the caption and the action it describes cannot drift apart.

**Speed and pause are a global time scale, not a script rate.** The player
publishes a scale and the frame loop multiplies every per-frame update by it, so
half speed slows the walking and the walk cycle too, and pause is the same
mechanism at zero. Had the player only throttled its own beats, a paused office
would have carried on walking about with the script stopped, and slow motion
would have looked like people hurrying between longer gaps.

**Restart is a cut, not a scene.** It abandons anything in flight, puts everyone
back at their desk instantly, clears the documents left lying around and issues
a ticket with its budgets back at zero. Walking everyone home would have been a
scenario of its own, and an agent half way across the office would otherwise
carry on to a destination belonging to the run that was just discarded.

**The states are the orchestrator's own, not a parallel invention.**
`js/tickets.js` is a port of `src/wjfyp/orchestrator/fsm.py`: the same nine
statuses, the same transition table, the same role-per-status map, and the same
budget overrides. That means the office animates `awaiting_test` rather than
something called "testing", and that an engineer's fourth failed test against a
cap of three produces `retry_cap_exceeded` and an escalation rather than a
fourth retry — because that substitution is what the real loop does. Layer 7
swaps the scripted driver for the live event stream, and the vocabulary already
matches, so there is no mapping layer in between to get wrong. The copy has to
be kept in step by hand until then; anything in `tickets.js` that disagrees with
`fsm.py` is a bug in `tickets.js`.

**The failure paths get as much attention as the happy one.** A visualization
that only ever showed a ticket being scoped and merged would misrepresent a
system whose interesting behaviour is mostly what it does when something goes
wrong. So a retry shows the shared correction budget counting down on the
engineer's badge, a timeout leaves them stuck at their desk under a filling
timer, an escalation walks them into the CTO's office carrying the ticket, and a
halt walks the ticket to the backlog and puts it down, where it stays for the
rest of the run.

**Behaviour sequences are polled, never chained on promises.** An errand like
"carry this to the backlog, put it down, walk back" is a list of steps that
`update()` advances. The reason is the headless capture: `?t=` winds the
simulation forward inside a synchronous loop, and a promise chain would not run
at all until that loop finished, so none of these paths could be verified from a
screenshot.

**Stride length and hip dip are derived from the rig, not tuned by eye.** A leg
swung forward is geometrically shorter in Y than a vertical one, so unless the
hips drop by exactly `legLength * (1 - cos(swing))` at that moment, the planted
foot hangs in the air and the figure skims the floor. The distance covered per
half cycle falls out of the same geometry, and the walk phase advances with
distance travelled rather than with time, so changing the walking speed cannot
make the feet skate. These three numbers are computed from one another in
`characters.js`; setting any of them independently is what makes a walk cycle
look wrong in a way that is hard to name.
