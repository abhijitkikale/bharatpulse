"""BharatPulse daily snap: a silent 56-second vertical video (1080x1920) of the day's market, made from the dashboard data after the end-of-day build.

  python tools/make_snap.py                 make today's video from the published dashboard (data/dashboard.json if the page cannot be read)
  python tools/make_snap.py --source local  use data/dashboard.json
  python tools/make_snap.py --preview       summary, cover and four sample frames only (no video)
  python tools/make_snap.py --telegram      also send the video to your Telegram chat for a quick look before you upload it
  python tools/make_snap.py --music FILE    add this music (default: the first .mp3 in the project folder; --no-music for a silent video only)

Output in data/snap/YYYY-MM-DD/: snap.mp4 (no audio track), snap_music.mp4 (the same with the music, faded in and out, when there is a music file), cover.png, caption.txt (title, description, hashtags,
disclaimer), timing.txt (scene times in seconds, for syncing sound), snap.json (the day's summary). data/snap_history/ keeps a small record per day so the next
day's video can say what changed. Nothing is uploaded; everything stays on this PC."""
import argparse
import functools
import http.server
import json
import pathlib
import subprocess
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
import snap_data  # noqa: E402
import stream_levels  # noqa: E402

PAGE_DIR = ROOT / "tools" / "snap_page"
PAGES = "https://abhijitkikale.github.io/bharatpulse/"
HISTORY = ROOT / "data" / "snap_history"
FPS, DUR = 30, snap_data.DUR
SCENES = [(name, a, b) for _, name, a, b in snap_data.TIMELINE]


def load_data(source):
    if source in ("auto", "pages"):
        try:
            import requests
            return stream_levels.parse_pages(requests.get(PAGES, timeout=40).text), "the published dashboard"
        except Exception as e:
            if source == "pages":
                raise
            print("could not read the published page (" + type(e).__name__ + "), using data/dashboard.json")
    return json.loads((ROOT / "data" / "dashboard.json").read_text(encoding="utf-8")), "data/dashboard.json"


def previous_snapshot(day):
    try:
        files = sorted(f for f in HISTORY.glob("*.json") if f.stem < str(day))
        return json.loads(files[-1].read_text(encoding="utf-8")) if files else None
    except Exception:
        return None


def find_music(arg):
    """The music file to use: --music FILE, else the first .mp3 in the project folder (or data/snap_music), else None."""
    if arg:
        return pathlib.Path(arg)
    for folder in (ROOT, ROOT / "data" / "snap_music"):
        files = sorted(folder.glob("*.mp3")) if folder.exists() else []
        if files:
            return files[0]
    return None


def music_credit(path):
    """'Music: Langoos by Moavii (Free To Use, freetouse.com)' from a file name like 'Moavii - Langoos (freetouse.com).mp3'."""
    import re
    stem = re.sub(r"\s*\(.*?\)", "", pathlib.Path(path).stem).strip()
    artist, _, track = stem.partition(" - ")
    site = re.search(r"\(([^)]+)\)", pathlib.Path(path).stem)
    src = f" ({site.group(1)})" if site else ""
    return f"Music: {track or stem}" + (f" by {artist}" if track else "") + src + (" | Free To Use: https://freetouse.com/music" if "freetouse" in pathlib.Path(path).name.lower() else "")


def mix_music(video, music, out, start=0.0):
    """Copy the video as it is and add the music: looped if shorter than the video, faded in over 0.6 s and out over the last 1.7 s."""
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(video), "-ss", str(start), "-stream_loop", "-1", "-i", str(music), "-t", str(DUR),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-af", f"afade=t=in:st=0:d=0.6,afade=t=out:st={DUR - 1.7}:d=1.7", "-shortest",
                    "-movflags", "+faststart", str(out)], check=True)


def serve(out_dir):
    class H(http.server.SimpleHTTPRequestHandler):
        def translate_path(self, path):
            p = path.split("?")[0]
            return str(out_dir / "snap.json") if p == "/snap.json" else super().translate_path(path)

        def log_message(self, *a):
            pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(H, directory=str(PAGE_DIR)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def render(out_dir, preview=False):
    """Run the page frame by frame in headless Chrome. Writes cover.png and, unless preview, snap.mp4 (H.264, silent)."""
    from playwright.sync_api import sync_playwright
    srv = serve(out_dir)
    url = f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    ff = None
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(channel="chrome", headless=True)
            except Exception:
                browser = p.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
            page.goto(url)
            page.wait_for_function("window.snapReady === true", timeout=30000)
            page.wait_for_timeout(400)
            page.evaluate("t => window.snapSetT(t)", 1.8)
            page.screenshot(path=str(out_dir / "cover.png"), type="png")
            if preview:
                for t in [round(a + 3.4, 1) for _, a, _ in SCENES[:-1]] + [55.0]:
                    page.evaluate("t => window.snapSetT(t)", t)
                    page.screenshot(path=str(out_dir / f"frame_{t:05.1f}.png"), type="png")
            else:
                import imageio_ffmpeg
                out = out_dir / "snap.mp4"
                ff = subprocess.Popen([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                                       "-an", "-vf", "scale=in_range=pc:out_range=tv,format=yuv420p", "-c:v", "libx264", "-color_range", "tv", "-preset", "medium", "-crf", "20", "-movflags", "+faststart", str(out)], stdin=subprocess.PIPE)
                n = int(DUR * FPS)
                for i in range(n):
                    page.evaluate("t => window.snapSetT(t)", i / FPS)
                    ff.stdin.write(page.screenshot(type="jpeg", quality=92))
                ff.stdin.close()
                ff.wait()
            browser.close()
    finally:
        srv.shutdown()
        if ff is not None and ff.poll() is None:
            ff.kill()


def video_ok(path):
    """True when the whole file decodes without a single error and is about as long as planned (a broken render once showed one frame for the whole video)."""
    import imageio_ffmpeg
    r = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-i", str(path), "-f", "null", "-"], capture_output=True, text=True, errors="replace")
    bad = [l for l in r.stderr.splitlines() if "NAL" in l or "Invalid data" in l or "error while decoding" in l.lower()]
    stamps = [l for l in r.stderr.splitlines() if "time=" in l]
    secs = 0.0
    if stamps:
        try:
            h, m, sec = stamps[-1].split("time=")[1].split()[0].split(":")
            secs = int(h) * 3600 + int(m) * 60 + float(sec)
        except Exception:
            pass
    return not bad and secs >= DUR - 1.0


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")        # the rupee sign in the headline breaks a Windows console otherwise
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["auto", "pages", "local"], default="auto")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--music", help="music file to add (default: the first .mp3 in the project folder)")
    ap.add_argument("--music-start", type=float, default=0.0, help="start the music this many seconds into the track")
    ap.add_argument("--no-music", action="store_true")
    a = ap.parse_args()
    D, src = load_data(a.source)
    day = snap_data._fmt_date(D)
    snap = snap_data.build(D, previous_snapshot(day))
    out_dir = ROOT / "data" / "snap" / snap["date"]
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "snap.json").write_text(json.dumps(snap, indent=1), encoding="utf-8")
    HISTORY.mkdir(parents=True, exist_ok=True)
    (HISTORY / (snap["date"] + ".json")).write_text(json.dumps(snap_data.snapshot_for_history(D, snap)), encoding="utf-8")
    title, desc, tags = snap_data.caption(snap)
    music = None if (a.no_music or a.preview) else find_music(a.music)
    if music is not None and music.exists():
        desc += "\n" + music_credit(music)
    else:
        music = None
    (out_dir / "caption.txt").write_text(f"{title}\n\n{desc}\n\n{tags}\n", encoding="utf-8")
    (out_dir / "timing.txt").write_text(f"Scene timings (seconds), total {DUR:.1f}, {FPS} fps, no audio track in snap.mp4\n" + "\n".join(f"{n:<20} {s:>5.1f} - {e:>5.1f}" for n, s, e in SCENES) + "\n", encoding="utf-8")
    print(f"data: {src} | session {snap['label']} | headline: {snap['hook']['sub']}")
    render(out_dir, preview=a.preview)
    if a.preview:
        print("preview written to", out_dir)
        return
    mp4 = out_dir / "snap.mp4"
    if not video_ok(mp4):                                              # never send a broken file: render once more, then stop
        print("the video has decoding errors: rendering again")
        render(out_dir, preview=False)
        if not video_ok(mp4):
            raise SystemExit("the video is still broken after a second render; nothing was sent")
    print(f"video: {mp4} ({mp4.stat().st_size / 1e6:.1f} MB)")
    if music is not None:
        final = out_dir / "snap_music.mp4"
        mix_music(mp4, music, final, a.music_start)
        print(f"with music ({music.name}): {final} ({final.stat().st_size / 1e6:.1f} MB)")
        mp4 = final
    if a.telegram:
        sys.path.insert(0, str(ROOT / "tools"))
        import telegram_notify
        print("telegram:", telegram_notify.send_video(str(mp4), title + "\n(for your review before you upload)"))


if __name__ == "__main__":
    main()
