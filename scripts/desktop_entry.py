"""Frozen desktop entry and an isolated smoke probe for the shipped binary."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


def smoke(output: Path, model_cache: Path | None = None):
    import geoask as g
    import geoask_credentials as credentials
    from geoask_places import search_place
    from geoask_gui import GeoaskApp

    report = {"version": g.__version__, "frozen": bool(getattr(sys, "frozen", False))}
    with tempfile.TemporaryDirectory(prefix="geoask-desktop-smoke-") as folder:
        credentials.connection_file = lambda: Path(folder) / "connection.dat"
        settings = g.read_settings(g.DEFAULT_CONFIG)
        settings.cache_dir = Path(folder) / "cache"
        with g.load_data(settings) as con:
            count = con.execute("SELECT count(*) FROM poi").fetchone()[0]
            assert count > 0
            rows = search_place(con, "鸟巢", (116.46, 39.92), 10, settings)
            assert any(row['name'] == '国家体育场' for row in rows)
        report.update(poi_rows=count, named_destination=True, spatial=True)
        credentials.save_connection(settings.llm.model_dump(), settings.map.model_dump(),
                                    settings.routing.model_dump(), "desktop-smoke-not-a-real-key")
        assert credentials.saved_api_key(settings.llm.model_dump()) == "desktop-smoke-not-a-real-key"
        credentials.clear_connection()
        report["local_credentials"] = True
        if model_cache:
            settings.cache_dir = model_cache.resolve()
            mapper = g.SemanticMapper(['cafe', 'restaurant', 'park', 'fitness_centre'], settings)
            assert mapper.match('咖啡').category == 'cafe'
            report["real_embedding"] = True
        app = GeoaskApp()
        try:
            app.update()
            app.open_settings()
            app.update()
            assert app.apply_settings_button.winfo_ismapped()
            report["gui"] = True
        finally:
            app.close()
    report["passed"] = True
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", type=Path)
    parser.add_argument("--model-cache", type=Path)
    args = parser.parse_args()
    if args.self_test:
        try:
            smoke(args.self_test, args.model_cache)
        except Exception:
            import traceback
            args.self_test.write_text(json.dumps({"passed": False, "traceback": traceback.format_exc()},
                                                 ensure_ascii=False, indent=2), encoding="utf-8")
            raise SystemExit(1)
        return
    try:
        from geoask_gui import main as gui_main
        gui_main()
    except Exception:
        from geoask_runtime import get_logger
        get_logger().exception("Desktop startup failed")
        from tkinter import messagebox
        messagebox.showerror("geoask 无法启动", "程序启动失败，请查看用户缓存目录 geoask/logs 中的日志。")
        raise


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
