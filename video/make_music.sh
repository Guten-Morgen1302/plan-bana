#!/usr/bin/env bash
# Generates out/music.wav: 120 BPM beat that drops at 6 s (after the cold open) and fades out at the end.
set -euo pipefail
D=${1:-138.2}
K='mod(t,0.5)'
R='if(lt(mod(t,8),2),55,if(lt(mod(t,8),4),43.65,if(lt(mod(t,8),6),65.41,49)))'
PUMP="(0.3+0.7*min(1,$K*4))"
KICK="0.9*sin(2*PI*50*$K+7*(1-exp(-$K*38)))*exp(-$K*9)"
HAT="0.10*(2*random(0)-1)*exp(-mod(t+0.25,0.5)*80)"
SNARE="(0.22*(2*random(1)-1)+0.12*sin(2*PI*185*mod(t-0.5,1)))*exp(-mod(t-0.5,1)*22)*gte(t-0.5,0)"
BASS="0.30*sin(2*PI*$R*t)*$PUMP"
PAD="0.07*(sin(2*PI*$R*4*t)+0.6*sin(2*PI*$R*6*t)+0.5*sin(2*PI*$R*5.04*t))*$PUMP"
RISER="(0.10*(2*random(2)-1)+0.06*sin(2*PI*(90+45*t)*t))*(t/6)"
EXPR="if(lt(t,6),$RISER,$KICK+$HAT+$SNARE+$BASS+$PAD)*0.55"
ffmpeg -v error -y -f lavfi -i "aevalsrc='$EXPR':s=44100:d=$D" \
  -af "lowpass=f=9000,afade=t=in:d=0.5,afade=t=out:st=$(echo "$D-4" | bc 2>/dev/null || python -c "print($D-4)"):d=4,alimiter=limit=0.8" out/music.wav
