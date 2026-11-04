#!/bin/bash
# An audible alarm that can wake a sleeping person, for when an
# unattended batch fails and a phone push would not be enough.
#
# A push notification assumes someone is looking at a screen. This does
# not: it forces the sink unmuted, then loops a struck-bell arpeggio
# until it is stopped.
#
# It CRESCENDOS rather than starting at full volume. The first cycles are
# quiet enough to surface someone gently, and it reaches full volume
# after about 75 seconds if they sleep through it. A siren at full blast
# from the first second was the earlier version and was rejected for
# being horrible, which matters: an alarm unpleasant enough to be
# disabled protects nothing.
#
# The original volume and mute state are saved and restored, so a false
# alarm does not leave the machine at full blast.
#
# Usage:
#   alarm.sh start "reason shown on screen"   loops until stopped
#   alarm.sh stop                             stops it, restores volume
#   alarm.sh test [seconds]                   short burst, default 4
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CHIME="$HERE/chime.wav"
STATE="/tmp/wjfyp-alarm"
STOP="$STATE/stop"
SAVED="$STATE/saved-volume"
REASON="$STATE/reason"
SINK="@DEFAULT_AUDIO_SINK@"
# Volume above 1.0 is PipeWire's software boost. The alarm walks from
# SOFT to LOUD over the first minute or so rather than jumping.
SOFT=0.45
LOUD=1.5
STEP=0.08
# A safety ceiling. If nobody stops it, it gives up rather than screaming
# at an empty house for hours.
MAX_MIN=45

mkdir -p "$STATE"

save_volume() {
  wpctl get-volume "$SINK" 2>/dev/null > "$SAVED" || echo "Volume: 1.0" > "$SAVED"
}
restore_volume() {
  [ -f "$SAVED" ] || return 0
  v=$(grep -oE '[0-9]+\.[0-9]+' "$SAVED" | head -1)
  [ -n "$v" ] && wpctl set-volume "$SINK" "$v" 2>/dev/null
  grep -q MUTED "$SAVED" && wpctl set-mute "$SINK" 1 2>/dev/null
  return 0
}

case "${1:-}" in
  stop)
    touch "$STOP"
    # Kill any running loop, filtered by comm so an interactive shell
    # whose command line mentions this script is never matched.
    for p in $(ps -eo pid,comm,args --no-headers | awk '$2=="bash" && /alarm[.]sh start/ {print $1}'); do
      kill "$p" 2>/dev/null
    done
    pkill -x paplay 2>/dev/null
    # Only dialogs this script raised, matched on the title it sets.
    for z in $(ps -eo pid,comm,args --no-headers | awk '$2=="zenity" && /WJ-FYP BATCH ALARM/ {print $1}'); do
      kill "$z" 2>/dev/null
    done
    sleep 1
    restore_volume
    echo "alarm stopped, volume restored to $(wpctl get-volume "$SINK" 2>/dev/null)"
    ;;
  test)
    secs="${2:-4}"
    save_volume
    wpctl set-mute "$SINK" 0 2>/dev/null
    wpctl set-volume "$SINK" "$SOFT" 2>/dev/null
    notify-send -u critical "WJ-FYP alarm test" "This is a test. No action needed." 2>/dev/null
    timeout "$secs" paplay "$CHIME" 2>/dev/null
    restore_volume
    echo "test done, ${secs}s at volume $SOFT, restored to $(wpctl get-volume "$SINK" 2>/dev/null)"
    ;;
  start)
    why="${2:-the batch needs attention}"
    rm -f "$STOP"
    echo "$why" > "$REASON"
    save_volume
    trap 'restore_volume; exit 0' EXIT INT TERM
    wpctl set-mute "$SINK" 0 2>/dev/null
    wpctl set-volume "$SINK" "$LOUD" 2>/dev/null
    notify-send -u critical "WJ-FYP BATCH ALARM" "$why" 2>/dev/null
    # A dialog with a stop button, because remembering a shell command
    # half asleep is not a dismissal mechanism. Clicking it writes the
    # stop file, which is the same thing `alarm.sh stop` does, so the
    # button and the command cannot disagree.
    ( zenity --warning --title="WJ-FYP BATCH ALARM" \
        --text="$why" --ok-label="STOP ALARM" --width=520 >/dev/null 2>&1
      touch "$STOP" ) &
    gui=$!
    trap 'kill $gui 2>/dev/null; restore_volume; exit 0' EXIT INT TERM
    deadline=$(( $(date +%s) + MAX_MIN * 60 ))
    vol=$SOFT
    while [ ! -f "$STOP" ] && [ "$(date +%s)" -lt "$deadline" ]; do
      wpctl set-volume "$SINK" "$vol" 2>/dev/null
      # Played in the BACKGROUND and polled, so pressing STOP is acted on
      # within a fifth of a second. Running paplay in the foreground meant
      # the loop could not re-check the stop file until the current 5.4
      # second chime had finished, so the alarm kept ringing for up to six
      # seconds after the button was pressed, and the volume restore then
      # raced the still-running loop and left the machine at whatever the
      # crescendo had reached. Both were observed on the first real test.
      paplay "$CHIME" 2>/dev/null &
      pp=$!
      while kill -0 "$pp" 2>/dev/null; do
        if [ -f "$STOP" ]; then kill "$pp" 2>/dev/null; break; fi
        sleep 0.2
      done
      wait "$pp" 2>/dev/null
      [ -f "$STOP" ] && break
      vol=$(awk -v v="$vol" -v s="$STEP" -v m="$LOUD" 'BEGIN{v+=s; if(v>m)v=m; printf "%.2f", v}')
      sleep 0.4
    done
    kill "$gui" 2>/dev/null
    restore_volume
    ;;
  *)
    echo "usage: alarm.sh start \"reason\" | stop | test [seconds]" >&2
    exit 2
    ;;
esac
