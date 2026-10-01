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
| `1` | send everyone to the drawing board |
| `0` | send everyone back to their desks |

`1` and `0` are a test harness for Layer 3, not a scenario. Layer 5 owns scripted
choreography and its playback controls; these two keys exist only so a walk can
be triggered and watched before that exists.

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
| 4 | Behaviour state machine and idle quirks (coffee, fridge, water cooler) | |
| 5 | Scripted scenario: ticket scoping at the drawing board, with playback controls | |
| 6 | Observation — click an agent for role and current action, event log | |
| 7 | Replace the scripted timeline with the live orchestrator websocket stream | deferred |

## Files

| File | Holds |
|---|---|
| `js/layout.js` | The floor plan as data: walls, zones, furniture, doors, station anchors. Nothing else hardcodes a coordinate. |
| `js/characters.js` | The agent rig and its poses. Joints, not a welded mesh, so a figure can sit down and later walk. |
| `js/agents.js` | The roster and placing it at stations. Identities match the orchestrator's real agent ids. |
| `js/build.js` | Geometry builders that turn the layout into meshes. All primitives, no external assets. |
| `js/palette.js` | Every colour in one place, including the role-tier colours shared with the 2D dashboard. |
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

**Stride length and hip dip are derived from the rig, not tuned by eye.** A leg
swung forward is geometrically shorter in Y than a vertical one, so unless the
hips drop by exactly `legLength * (1 - cos(swing))` at that moment, the planted
foot hangs in the air and the figure skims the floor. The distance covered per
half cycle falls out of the same geometry, and the walk phase advances with
distance travelled rather than with time, so changing the walking speed cannot
make the feet skate. These three numbers are computed from one another in
`characters.js`; setting any of them independently is what makes a walk cycle
look wrong in a way that is hard to name.
