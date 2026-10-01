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

### URL parameters

| Parameter | Does |
|---|---|
| `?anchors=1` | Show station anchor markers at load, the quickest way to check every station still lands on the chair or prop it belongs to |
| `?labels=0` | Hide the name tags |
| `?focus=x,z` | Centre the view on a point on the floor, e.g. `?focus=-8,2.6` |
| `?zoom=n` | Zoom factor, roughly 0.55 to 4.5 |
| `?still=1` | Draw a single frame instead of running the loop, and expose it for capture (see below) |

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
| 3 | Navigation — waypoint graph, path following, walk animation, sit/stand | |
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
