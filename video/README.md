# Plan Bana demo video

Motion graphics in [Remotion](https://www.remotion.dev) around the real 30 Sep live recording. 1920×1080, 30 fps, ~2:18.

```bash
cd video
npm install
bash cut_clips.sh                      # needs docs/demo/telegram demo.mp4 (not in git)
npx remotion studio src/index.ts       # preview + scrub in the browser
npx remotion render src/index.ts PlanBana out/plan-bana-silent.mp4 --codec=h264 --crf=18
bash make_music.sh 138.2               # synthesized beat → out/music.wav
ffmpeg -i out/plan-bana-silent.mp4 -i out/music.wav -c:v copy -c:a aac -b:a 192k -shortest out/plan-bana.mp4
```

Honesty rules:
- Every phone screen is the unedited 30 Sep recording, only sped up. The home address is blurred.
- The chaining scene uses a separately logged real run (8 Oct, Comedy Cartel at Glocal → FREE tables 0.4–0.8 km), and says so on screen.

| Scene | Source | Clip |
|---|---|---|
| Cold open, logo, problem | animation | |
| Chat | 0:02–1:26 @6× | c1_chat |
| Samjha | 1:28–1:48 @2× | c2_plan |
| Plans + 6 checks | 2:06–2:22 @2× | c3_plans |
| Chaining | animation (real 8 Oct run) | |
| Vote → Book | 2:24–3:08 @2.4× | c4_decide |
| Final card + map | 3:04–3:22 @2× | c5_map |
| Swiggy app | 3:26–3:42 @1.6× | c6_app (address blurred) |
| WhatsApp | 3:52–4:06 @1.6× | c7_whatsapp |
| "Doneee party" | 4:08–4:18 @1.4× | c8_party |
| Stats, end card | animation | |
