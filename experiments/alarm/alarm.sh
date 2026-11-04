#!/bin/bash
# An audible alarm that can wake a sleeping person, for when unattended
# work fails and a phone push would not be enough.
#
# A push notification assumes someone is looking at a screen. This does
# not: it forces the sink unmuted, then loops a struck-bell arpeggio
# until it is stopped, and raises a dialog with a STOP ALARM button
# because remembering a shell command half asleep is not a dismissal
# mechanism.
#
# It CRESCENDOS rather than starting at full volume. The first version
# was a full-scale two-tone siren from the first second and was rejected
# for being horrible, which matters: an alarm unpleasant enough to be
# switched off protects nothing.
#
# MULTI-TENANT. More than one project can use this at once, so every
# instance is namespaced by a label. Without that, two callers sharing
# one state directory would mean either one silencing the other, one's
# volume restore clobbering the other's saved level, and no way to tell
# from the dialog which project was asking for you.
#
# Usage:
#   alarm.sh start "reason" [label]    loops until stopped
#   alarm.sh stop [label]              stops that label, restores volume
#   alarm.sh test [seconds] [label]    short burst
#
# The label defaults to WJFYP, so existing callers keep working.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHIME="$HERE/chime.wav"
SINK="@DEFAULT_AUDIO_SINK@"

# The alarm walks from SOFT to LOUD over about 75 seconds.
SOFT=0.45
LOUD=1.5
STEP=0.08
# A safety ceiling, so it gives up rather than ringing at an empty house.
MAX_MIN=45

# The volume to put back is SHARED across tenants and written once, by
# whichever alarm rings first. Per-label saves would mean the second
# alarm recording the first one's crescendo as "the original" and
# restoring the machine to that.
ORIG="/tmp/alarm-original-volume"

label_state() { echo "/tmp/alarm-${1}"; }

save_volume() {
  [ -f "$ORIG" ] && return 0
  wpctl get-volume "$SINK" 2>/dev/null > "$ORIG" || echo "Volume: 1.0" > "$ORIG"
}

others_running() {
  # Any OTHER label with a live alarm loop. Checked by pid file rather
  # than by matching command lines, which is what made the earlier
  # supervisor kill the wrong processes.
  local me="$1" d pid found=0
  for d in /tmp/alarm-*/; do
    [ -d "$d" ] || continue
    [ "$d" = "$(label_state "$me")/" ] && continue
    pid=$(cat "$d/pid" 2>/dev/null) || continue
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && found=1
  done
  [ "$found" = 1 ]
}

restore_volume() {
  local me="$1"
  # Only the LAST alarm standing puts the volume back, or it would be
  # reset underneath another one still trying to be heard.
  others_running "$me" && return 0
  [ -f "$ORIG" ] || return 0
  local v
  v=$(grep -oE '[0-9]+\.[0-9]+' "$ORIG" | head -1)
  [ -n "$v" ] && wpctl set-volume "$SINK" "$v" 2>/dev/null
  grep -q MUTED "$ORIG" && wpctl set-mute "$SINK" 1 2>/dev/null
  rm -f "$ORIG"
  return 0
}

case "${1:-}" in
  stop)
    LABEL="${2:-WJFYP}"
    STATE=$(label_state "$LABEL")
    mkdir -p "$STATE"
    touch "$STATE/stop"
    # The loop kills its own audio within 0.2s of seeing the stop file,
    # so there is no need to pkill paplay here - doing that would also
    # silence another project's alarm.
    for i in 1 2 3 4 5 6; do
      pid=$(cat "$STATE/pid" 2>/dev/null) || break
      [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || break
      sleep 0.5
    done
    pid=$(cat "$STATE/pid" 2>/dev/null)
    [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null
    gpid=$(cat "$STATE/guipid" 2>/dev/null)
    [ -n "${gpid:-}" ] && kill "$gpid" 2>/dev/null
    restore_volume "$LABEL"
    rm -f "$STATE/pid" "$STATE/guipid"
    echo "$LABEL alarm stopped. volume now $(wpctl get-volume "$SINK" 2>/dev/null)"
    ;;
  test)
    secs="${2:-6}"
    LABEL="${3:-WJFYP}"
    save_volume
    wpctl set-mute "$SINK" 0 2>/dev/null
    wpctl set-volume "$SINK" "$SOFT" 2>/dev/null
    notify-send -u critical "$LABEL alarm test" "This is a test. No action needed." 2>/dev/null
    timeout "$secs" paplay "$CHIME" 2>/dev/null
    restore_volume "$LABEL"
    echo "$LABEL test done, ${secs}s at $SOFT, volume now $(wpctl get-volume "$SINK" 2>/dev/null)"
    ;;
  start)
    why="${2:-work needs your attention}"
    LABEL="${3:-WJFYP}"
    STATE=$(label_state "$LABEL")
    mkdir -p "$STATE"
    rm -f "$STATE/stop"
    echo "$$" > "$STATE/pid"
    echo "$why" > "$STATE/reason"
    save_volume
    wpctl set-mute "$SINK" 0 2>/dev/null
    notify-send -u critical "$LABEL ALARM" "$why" 2>/dev/null
    # zenity is backgrounded DIRECTLY, not inside a subshell, so $! is
    # zenity's own pid. Wrapped in a subshell it was the subshell's pid,
    # and killing that left the dialog on screen after the alarm stopped,
    # which is what the two-tenant test caught.
    #
    # Its exit IS the stop signal: the loop below watches the pid rather
    # than having the subshell touch the stop file, which is what made
    # the wrapper necessary in the first place.
    gui=""
    if command -v zenity >/dev/null 2>&1; then
      zenity --warning --title="$LABEL ALARM" \
        --text="$why" --ok-label="STOP ALARM" --width=520 >/dev/null 2>&1 &
      gui=$!
      sleep 0.5
      # If the dialog could not start - no display, no zenity - the alarm
      # must still ring. Treating a failed dialog as a press would
      # silence it instantly, which is the worst possible failure here.
      kill -0 "$gui" 2>/dev/null || gui=""
      [ -n "$gui" ] && echo "$gui" > "$STATE/guipid"
    fi
    trap 'kill $gui 2>/dev/null; restore_volume "$LABEL"; rm -f "$STATE/pid" "$STATE/guipid"; exit 0' EXIT INT TERM
    deadline=$(( $(date +%s) + MAX_MIN * 60 ))
    vol=$SOFT
    while [ ! -f "$STATE/stop" ] && [ "$(date +%s)" -lt "$deadline" ]; do
      if [ -n "$gui" ] && ! kill -0 "$gui" 2>/dev/null; then break; fi
      wpctl set-volume "$SINK" "$vol" 2>/dev/null
      # Played in the BACKGROUND and polled, so STOP is acted on within a
      # fifth of a second. In the foreground the loop could not re-check
      # the stop file until the current 5.4 second chime finished, so the
      # alarm kept ringing for up to six seconds after the button was
      # pressed, and the volume restore then raced the still-running loop
      # and left the machine at whatever the crescendo had reached. Both
      # observed on the first real test.
      paplay "$CHIME" 2>/dev/null &
      pp=$!
      while kill -0 "$pp" 2>/dev/null; do
        if [ -f "$STATE/stop" ]; then kill "$pp" 2>/dev/null; break; fi
        if [ -n "$gui" ] && ! kill -0 "$gui" 2>/dev/null; then kill "$pp" 2>/dev/null; break; fi
        sleep 0.2
      done
      wait "$pp" 2>/dev/null
      [ -f "$STATE/stop" ] && break
      if [ -n "$gui" ] && ! kill -0 "$gui" 2>/dev/null; then break; fi
      vol=$(awk -v v="$vol" -v s="$STEP" -v m="$LOUD" 'BEGIN{v+=s; if(v>m)v=m; printf "%.2f", v}')
      sleep 0.4
    done
    kill "$gui" 2>/dev/null
    restore_volume "$LABEL"
    rm -f "$STATE/pid" "$STATE/guipid"
    ;;
  *)
    echo "usage: alarm.sh start \"reason\" [label] | stop [label] | test [seconds] [label]" >&2
    exit 2
    ;;
esac
