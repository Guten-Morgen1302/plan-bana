#!/usr/bin/env bash
# Cuts docs/demo/telegram demo.mp4 (the 30 Sep live recording) into the sped-up clips the video uses.
# name  source-start  source-end  speed
set -euo pipefail
cd "$(dirname "$0")/.."
IN="docs/demo/telegram demo.mp4"
mkdir -p video/public/clips
cut() { ffmpeg -v error -y -ss "$2" -to "$3" -i "$IN" -an -vf "setpts=PTS/$4,fps=30,scale=576:1296" \
  -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p "video/public/clips/$1.mp4"; }
cut c1_chat 2 86 6          # the messy chat
cut c2_plan 88 108 2        # /plan, date question, Samjha
cut c3_plans 126 142 2      # planning → 3 plans
cut c4_decide 144 188 2.4   # vote, lock, RSVP, guests, Book, booked
cut c5_map 184 202 2        # final card, pinned, Google Maps
cut c6_app 206 222 1.6      # Swiggy app: My Bookings, "Your table is booked!"
cut c7_whatsapp 232 246 1.6 # Dineout WhatsApp confirmation
cut c8_party 248 258.5 1.4  # "Doneee party"
# blur the home address shown on the Swiggy home screen (clip seconds 2.15–4.1)
ffmpeg -v error -y -i video/public/clips/c6_app.mp4 -filter_complex \
  "[0:v]split[a][b];[b]crop=576:220:0:50,boxblur=24:3[bl];[a][bl]overlay=0:50:enable='between(t,2.15,4.1)'" \
  -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p video/public/clips/c6_app.tmp.mp4
mv video/public/clips/c6_app.tmp.mp4 video/public/clips/c6_app.mp4
