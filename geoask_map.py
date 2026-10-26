"""Small XYZ raster map: bounded background downloads, disk cache, WGS84 overlays."""
from __future__ import annotations

from collections import OrderedDict
import hashlib
from io import BytesIO
import math
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Thread
import time
import tkinter as tk
from urllib.request import Request, urlopen

from PIL import Image, ImageTk

import geoask as g
from geoask_runtime import atomic_output, drain_events, get_logger, user_cache_dir


def world_pixel(lon, lat, zoom):
    lat = max(-85.05112878, min(85.05112878, lat))
    size = 256 * 2**zoom
    return ((lon + 180) / 360 * size,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * size)


def pixel_coordinate(x, y, zoom):
    size = 256 * 2**zoom
    return ((x / size * 360) % 360 - 180,
            math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * max(0, min(size, y)) / size)))))


def validate_tile(data):
    if len(data) > 2_000_000:
        raise ValueError("Tile too large")
    with Image.open(BytesIO(data)) as image:
        if image.size != (256, 256):
            raise ValueError("XYZ tile must be 256x256")
        image.verify()
    # verify() alone does not decode JPEG pixels and accepts some truncated files.
    with Image.open(BytesIO(data)) as image:
        image.load()


def fetch_tile(path, settings, z, x, y, opener=None):
    """Cache failures never discard a successfully downloaded, valid tile."""
    try:
        if path.is_file() and time.time() - path.stat().st_mtime < 7*86400:
            data = path.read_bytes()
            validate_tile(data)
            return data
    except (OSError, ValueError, SyntaxError):
        get_logger().warning("Ignoring unreadable or invalid cached tile")
    request = Request(settings.tile_url.format(z=z, x=x, y=y), headers={"User-Agent": settings.user_agent})
    with (opener or urlopen)(request, timeout=12) as response:
        data = response.read(2_000_001)
    validate_tile(data)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with atomic_output(path, mode="wb") as output:
            output.write(data)
    except OSError:
        get_logger().warning("Tile cache write failed; displaying downloaded image")
    return data


def prune_tile_cache(directory, *, max_bytes=128*1024*1024, max_age=7*86400):
    """Evict expired files then oldest files until the disk budget is met."""
    entries = []
    now = time.time()
    try:
        for path in Path(directory).rglob("*"):
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                if now-stat.st_mtime > max_age:
                    path.unlink(missing_ok=True)
                elif path.suffix == ".png":
                    entries.append((stat.st_mtime, stat.st_size, path))
            except OSError:
                get_logger().warning("Could not inspect/remove a cached tile; continuing")
    except OSError:
        get_logger().warning("Could not enumerate tile cache")
    total = sum(size for _, size, _ in entries)
    for _, size, path in sorted(entries):
        if total <= max_bytes:
            break
        try:
            path.unlink(missing_ok=True)
            total -= size
        except OSError:
            get_logger().warning("Could not remove a cached tile; continuing")


class SlippyMap(tk.Canvas):
    def __init__(self, parent, on_marker=None, on_position=None, **kwargs):
        super().__init__(parent, bg="#e9f0eb", highlightthickness=0, **kwargs)
        self.on_marker, self.on_position = on_marker, on_position
        self.center, self.zoom = (116.46, 39.92), 15
        self.markers, self.route, self.marker_positions = [], [], []
        self.photos = OrderedDict()
        self.pending, self.failed = set(), {}
        self.jobs, self.completed = Queue(maxsize=32), Queue()
        self.settings = g.MapSettings()
        self.cache_dir = user_cache_dir() / "tiles"
        self.generation = 0
        self.tiles_loaded, self.tile_error = 0, ""
        self.closed = False
        self._drag = None
        self.configure_map(self.settings, self.cache_dir)
        for _ in range(2):
            Thread(target=self._download, daemon=True, name="geoask-tiles").start()
        self.bind("<Configure>", lambda _: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<MouseWheel>", lambda e: self.zoom_at(1 if e.delta > 0 else -1, e.x, e.y))
        self.bind("<Button-4>", lambda e: self.zoom_at(1, e.x, e.y))
        self.bind("<Button-5>", lambda e: self.zoom_at(-1, e.x, e.y))
        self.bind("<Button-3>", self._position)
        controls = tk.Frame(self, bg="white", bd=0)
        for text, direction in (("+", 1), ("−", -1)):
            tk.Button(controls, text=text, font=("Segoe UI", 14), bg="white", fg="#087f6a", bd=0,
                      width=2, command=lambda d=direction: self.zoom_at(d), cursor="hand2").pack()
        controls.place(x=12, y=12)
        self.after(100, self._poll)

    def configure_map(self, settings: g.MapSettings, cache_dir: Path):
        self.settings, self.cache_dir = settings, cache_dir
        Thread(target=prune_tile_cache, args=(cache_dir,), daemon=True, name="geoask-tile-cleanup").start()
        self.provider = hashlib.sha256(settings.tile_url.encode()).hexdigest()[:16]
        self.generation += 1
        self.photos.clear()
        self.failed.clear()
        self.tile_error = ""
        if self.winfo_exists():
            self.redraw()

    def set_center(self, lon, lat, zoom=None):
        g.validate_location(lon, lat)
        self.center = lon, max(-85.05112878, min(85.05112878, lat))
        if zoom is not None:
            self.zoom = max(2, min(19, int(zoom)))
        self.redraw()

    def _map_point(self, lon, lat):
        if getattr(self.settings, "coordinate_system", "wgs84") == "gcj02":
            from geoask_amap import wgs84_to_gcj02
            return wgs84_to_gcj02(lon, lat)
        return lon, lat

    def _wgs_point(self, lon, lat):
        if getattr(self.settings, "coordinate_system", "wgs84") == "gcj02":
            from geoask_amap import gcj02_to_wgs84
            return gcj02_to_wgs84(lon, lat)
        return lon, lat

    def fit(self, points):
        if not points:
            return
        width, height = max(200, self.winfo_width()), max(150, self.winfo_height())
        # Unwrap longitude around the first point to fit date-line itineraries.
        anchor = points[0][0]
        normalized = [(anchor + math.remainder(lon-anchor, 360), lat) for lon, lat in points]
        max_zoom = 15 if len(set(points)) == 1 else 18
        map_point = getattr(self, "_map_point", lambda lon, lat: (lon, lat))
        wgs_point = getattr(self, "_wgs_point", lambda lon, lat: (lon, lat))
        for zoom in range(max_zoom, 1, -1):
            pixels = [world_pixel(*map_point(lon, lat), zoom) for lon, lat in normalized]
            xs, ys = [p[0] for p in pixels], [p[1] for p in pixels]
            if max(xs)-min(xs) < width-120 and max(ys)-min(ys) < height-100:
                lon, lat = wgs_point(*pixel_coordinate((min(xs)+max(xs))/2, (min(ys)+max(ys))/2, zoom))
                self.set_center(lon, lat, zoom)
                return
        self.set_center(*points[0], zoom=2)

    def screen_point(self, lon, lat):
        map_point = getattr(self, "_map_point", lambda x, y: (x, y))
        x, y = world_pixel(*map_point(lon, lat), self.zoom)
        cx, cy = world_pixel(*map_point(*self.center), self.zoom)
        size = 256 * 2**self.zoom
        dx = (x-cx+size/2) % size-size/2
        return self.winfo_width()/2+dx, self.winfo_height()/2+y-cy

    def _press(self, event):
        self._drag = (event.x, event.y, *world_pixel(*self._map_point(*self.center), self.zoom))

    def _move(self, event):
        if self._drag:
            x, y, cx, cy = self._drag
            self.center = self._wgs_point(*pixel_coordinate(cx-event.x+x, cy-event.y+y, self.zoom))
            self.redraw()

    def _release(self, event):
        if self._drag and math.hypot(event.x-self._drag[0], event.y-self._drag[1]) < 5:
            hits = [(math.hypot(event.x-x, event.y-y), key) for x, y, key in self.marker_positions]
            if hits and min(hits)[0] < 16 and self.on_marker:
                self.on_marker(min(hits)[1])
        self._drag = None

    def _position(self, event):
        if self.on_position:
            cx, cy = world_pixel(*self._map_point(*self.center), self.zoom)
            self.on_position(*self._wgs_point(*pixel_coordinate(cx+event.x-self.winfo_width()/2,
                                                                  cy+event.y-self.winfo_height()/2, self.zoom)))

    def zoom_at(self, direction, x=None, y=None):
        zoom = max(2, min(19, self.zoom+direction))
        if zoom == self.zoom:
            return
        x, y = (self.winfo_width()/2 if x is None else x), (self.winfo_height()/2 if y is None else y)
        cx, cy = world_pixel(*self._map_point(*self.center), self.zoom)
        factor = 2**(zoom-self.zoom)
        px = (cx+x-self.winfo_width()/2)*factor-x+self.winfo_width()/2
        py = (cy+y-self.winfo_height()/2)*factor-y+self.winfo_height()/2
        self.center, self.zoom = self._wgs_point(*pixel_coordinate(px, py, zoom)), zoom
        self.redraw()

    def redraw(self):
        if self.closed:
            return
        self.delete("all")
        width, height = self.winfo_width(), self.winfo_height()
        if width < 10 or height < 10:
            return
        cx, cy = world_pixel(*self._map_point(*self.center), self.zoom)
        left, top = cx-width/2, cy-height/2
        n = 2**self.zoom
        for tx in range(math.floor(left/256), math.floor((left+width)/256)+1):
            for ty in range(math.floor(top/256), math.floor((top+height)/256)+1):
                if not 0 <= ty < n:
                    continue
                key = self.generation, self.zoom, tx % n, ty
                x, y = tx*256-left, ty*256-top
                if key in self.photos:
                    self.create_image(x, y, image=self.photos[key], anchor="nw")
                    self.photos.move_to_end(key)
                else:
                    self.create_rectangle(x, y, x+256, y+256, outline="#dae5dd", fill="#edf3ee")
                    if key not in self.pending and self.failed.get(key, 0) < time.monotonic():
                        try:
                            self.jobs.put_nowait((key, self.settings, self.cache_dir / self.provider))
                            self.pending.add(key)
                        except Full:
                            pass
        if self.route:
            points = [self.screen_point(lon, lat) for lon, lat in self.route]
            if len(points) >= 2:
                self.create_line(*[v for p in points for v in p], fill="white", width=8, joinstyle="round")
                self.create_line(*[v for p in points for v in p], fill="#6366d8", width=4, joinstyle="round")
        self.marker_positions = []
        for marker in self.markers:
            x, y = self.screen_point(*marker["point"])
            if not -25 <= x <= width+25 or not -25 <= y <= height+25:
                continue
            chosen = marker.get("kind") in ("stop", "origin")
            color = "#d59124" if marker.get("kind") == "origin" else "#087f6a" if chosen else "#4b8ea0"
            radius = 11 if chosen else 7
            if marker.get("active"):
                self.create_oval(x-15, y-15, x+15, y+15, outline="#172d2a", width=2)
            self.create_oval(x-radius, y-radius, x+radius, y+radius, fill=color, outline="white", width=2)
            if chosen:
                self.create_text(x, y, text=marker.get("label", ""), fill="white", font=("Segoe UI", 9, "bold"))
            if marker.get("id"):
                self.marker_positions.append((x, y, marker["id"]))
        label = self.settings.attribution + "  |  滚轮缩放 · 拖动平移 · 右键设为起点"
        self.create_rectangle(0, height-24, width, height, fill="white", outline="")
        self.create_text(width-8, height-12, text=label, anchor="e", fill="#52695f", font=("Microsoft YaHei UI", 8))
        if self.tile_error:
            self.create_rectangle(8, height-52, min(width-8, 460), height-27, fill="#fff7df", outline="")
            self.create_text(16, height-40, text="底图暂不可用，可检查网络或更换底图接口；地点和路线仍保留。",
                             anchor="w", fill="#8a6421", font=("Microsoft YaHei UI", 8))

    def _download(self):
        while True:
            if self.closed:
                return
            try:
                job = self.jobs.get(timeout=1)
            except Empty:
                if self.closed:
                    return
                continue
            if job is None:
                return
            key, settings, directory = job
            _, z, x, y = key
            path = directory / str(z) / str(x) / f"{y}.png"
            try:
                data = fetch_tile(path, settings, z, x, y)
                self.completed.put((key, data))
            except Exception:
                get_logger().exception("Tile download failed (%s/%s/%s)", z, x, y)
                self.completed.put((key, None))

    def _handle_tile(self, event):
        key, data = event
        self.pending.discard(key)
        if key[0] != self.generation:
            return
        if data:
            try:
                with Image.open(BytesIO(data)) as image:
                    self.photos[key] = ImageTk.PhotoImage(image.convert("RGB"), master=self)
            except Exception:
                self.failed[key] = time.monotonic()+45
                self.tile_error = "unavailable"
                raise
            self.tiles_loaded += 1
            self.tile_error = ""
            while len(self.photos) > 100:
                self.photos.popitem(last=False)
        else:
            self.failed[key] = time.monotonic()+45
            self.tile_error = "unavailable"

    def _tile_error(self):
        self.tile_error = "unavailable"

    def _poll(self):
        if self.closed:
            return
        try:
            if drain_events(self.completed, self._handle_tile, self._tile_error):
                self.redraw()
        except Exception:
            get_logger().exception("Map redraw failed")
        finally:
            if not self.closed:
                self.after(100, self._poll)

    def close(self):
        self.closed = True
