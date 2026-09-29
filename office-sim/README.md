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

`?anchors=1` turns the anchor markers on at load, which is the quickest way to
check that every station still lands on the chair or prop it belongs to.

## Build layers

The prototype is built in layers, each one self-contained and verifiable before
the next starts.

| Layer | Adds | State |
|---|---|---|
| 1 | Static office shell — floor plan, low walls, furniture, zones, camera | done |
| 2 | The five agents as people, colour-coded by tier, at their home stations | |
| 3 | Navigation — waypoint graph, path following, walk animation, sit/stand | |
| 4 | Behaviour state machine and idle quirks (coffee, fridge, water cooler) | |
| 5 | Scripted scenario: ticket scoping at the drawing board, with playback controls | |
| 6 | Observation — click an agent for role and current action, event log | |
| 7 | Replace the scripted timeline with the live orchestrator websocket stream | deferred |

## Files

| File | Holds |
|---|---|
| `js/layout.js` | The floor plan as data: walls, zones, furniture, doors, station anchors. Nothing else hardcodes a coordinate. |
| `js/build.js` | Geometry builders that turn the layout into meshes. All primitives, no external assets. |
| `js/palette.js` | Every colour in one place, including the role-tier colours shared with the 2D dashboard. |
| `js/scene.js` | Renderer, orthographic camera, lighting, camera controls, frame loop. |
| `js/main.js` | Wiring only. |

## Two choices worth knowing about

**The camera is tilted, not straight down.** A literal 90-degree top-down view
of 3D people shows nothing but the tops of their heads. The camera sits about 35
degrees off vertical instead, which is what the Sims-style games actually do,
and it can be rotated within a band that never reaches eye level.

**The walls are 1.35m, not full height.** Low walls mean the camera never needs
a cutaway system to see inside rooms. The side effect is that a wall hides
roughly a metre of floor immediately behind it at this pitch, so floor labels
and low furniture are kept clear of that band.
