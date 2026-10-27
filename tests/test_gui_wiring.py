"""Real withdrawn Tk widgets, stubbed map/worker; no model, tiles or HTTP.

Linux CI runs these under Xvfb. Set GEOASK_REQUIRE_GUI=1 to make an unavailable
display an error rather than a local headless skip.
"""
from dataclasses import replace
import os
from queue import Queue
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest
import tkinter as tk
from tkinter import ttk

import geoask as g
import geoask_gui as gui
from geoask_service import PlannedStop, QueryRequest, QueryResult


@pytest.fixture(scope="module")
def application():
    monkeypatch = pytest.MonkeyPatch()
    class Map(tk.Frame):
        def __init__(self, parent, **kwargs):
            super().__init__(parent)
            self.configure_map, self.set_center, self.fit = MagicMock(), MagicMock(), MagicMock()
            self.redraw, self.close = MagicMock(), MagicMock()
            self.markers, self.route = [], []
            self.marker_positions = []
    monkeypatch.setattr(gui, "SlippyMap", Map)
    monkeypatch.setattr(gui, "Thread", MagicMock())
    monkeypatch.setattr(gui.messagebox, "showerror", MagicMock())
    try:
        instance = gui.GeoaskApp()
    except tk.TclError as exc:
        if os.getenv("GEOASK_REQUIRE_GUI") == "1" or "display" not in str(exc).lower():
            pytest.fail(f"Tk display required by CI: {exc}")
        pytest.skip(f"Tk display unavailable; use xvfb-run: {exc}")
    instance.withdraw()
    try:
        yield instance
    finally:
        instance.close()
        monkeypatch.undo()


@pytest.fixture
def app(application):
    # Reuse one Tcl interpreter, as the actual app does. Reset widgets and jobs
    # between cases without repeatedly destroying/reinitializing Tcl libraries.
    for name in ("settings_window", "import_window"):
        window = getattr(application, name)
        if window is not None and window.winfo_exists():
            window.destroy()
        setattr(application, name, None)
    application.jobs = Queue()
    application.api_key.set("")
    application._load_config(g.DEFAULT_CONFIG)
    yield application


def result():
    intent = g.GeoIntent(intent="咖啡", modifiers=[], place_name="同名咖啡")
    rows = [dict(name="同名咖啡", category="cafe", longitude=116.47+i*.001, latitude=39.925,
                 dist_m=800+i*50, name_rank=0, name_match_count=2) for i in range(2)]
    stop = PlannedStop(intent, g.CategoryMatch(None, "按名称", 0, 0), rows, (116.46, 39.92),
                       blocked_reason="请选择具体地点", resolution="name")
    request = QueryRequest("去同名咖啡", (116.46, 39.92), 1000, 10, planning=True)
    return QueryResult(request, intent, stop.match, [], rows, "SELECT ?", ["value"], 2, [stop])


def test_show_result_really_wires_candidates_fit_status_and_controls(app):
    value = result()
    app._show_result(value)
    app._set_busy(False)
    assert app.result is value and len(app.stop_combos) == 1
    assert app.stop_combos[0].get() == "请选择具体地点"
    assert "请选择具体地点" in app.status.get()
    points = app.map_view.fit.call_args.args[0]
    assert value.request.center in points
    assert all((r["longitude"], r["latitude"]) in points for r in value.rows)
    assert app.route_button.cget("state") == "disabled"
    assert len(app.table.get_children()) == 2
    app._show_result(replace(value, stops=[replace(value.stops[0], selected_index=1, blocked_reason="")]))
    app._set_busy(False)
    assert app.route_button.cget("state") == "normal"
    assert app.stop_combos[0].current() == 1 and "选择已完成" in app.status.get()


def test_result_event_and_new_request_ignore_stale_generation(app):
    value = result()
    app.auto_route.set(False)
    app._queue_job(("query", value.request))
    generation, kind, request = app.jobs.get_nowait()
    assert (kind, request) == ("query", value.request)
    app._handle_event((generation-1, "result", value))
    assert app.result is None
    app._handle_event((generation, "result", value))
    assert app.result is value and not app.busy
    app._queue_job(("reselect", (value, 0, 1)))
    assert app.jobs.get_nowait()[0] > generation
    app._handle_event((generation, "progress", "stale text"))
    assert app.status.get() != "stale text"


@pytest.mark.parametrize("button_tab", [0, 1])
def test_real_settings_apply_saves_both_tabs_without_resetting_map(app, button_tab):
    app.open_settings()
    dialog = app.settings_window
    tabs = next(c for c in dialog.winfo_children() if isinstance(c, ttk.Notebook))
    llm_panel, map_panel = tabs.winfo_children()
    entries = [c for c in llm_panel.winfo_children() if c.winfo_class() == "TEntry"]
    entries[1].delete(0, "end")
    entries[1].insert(0, "changed-model")
    entries[-1].insert(0, "session-secret-for-test")
    app.map_view.configure_map.reset_mock()
    app.road_route = NS(coordinates=[])
    route = app.road_route
    panel = [llm_panel, map_panel][button_tab]
    buttons = [c for c in panel.winfo_children() if isinstance(c, tk.Button)]
    buttons[-1].invoke()
    assert app.llm.model == "changed-model" and app.api_key.get() == "session-secret-for-test"
    assert not dialog.winfo_exists()
    app.map_view.configure_map.assert_not_called()
    assert app.road_route is route


def test_settings_validation_leaves_dialog_and_session_values_intact(app):
    app.api_key.set("original-key")
    app.open_settings()
    dialog = app.settings_window
    tabs = next(c for c in dialog.winfo_children() if isinstance(c, ttk.Notebook))
    llm_panel, map_panel = tabs.winfo_children()
    entries = [c for c in map_panel.winfo_children() if c.winfo_class() == "TEntry"]
    entries[0].delete(0, "end")
    entries[0].insert(0, "invalid-url")
    app.apply_settings_button.invoke()
    assert dialog.winfo_exists() and app.api_key.get() == "original-key"
    error = gui.messagebox.showerror.call_args.args[1]
    assert "底图" in error and "validation error" not in error and "pydantic.dev" not in error


def test_saved_profile_restores_endpoint_model_and_key_then_clears(app):
    import geoask_credentials as credentials
    app.open_settings()
    tabs = next(c for c in app.settings_window.winfo_children() if isinstance(c, ttk.Notebook))
    panel = tabs.winfo_children()[0]
    entries = [c for c in panel.winfo_children() if c.winfo_class() == 'TEntry']
    entries[0].delete(0, 'end')
    entries[0].insert(0, 'https://example.test/v1')
    entries[1].delete(0, 'end')
    entries[1].insert(0, 'persisted-model')
    entries[-1].insert(0, 'test-persisted-key')
    app.apply_settings_button.invoke()
    assert credentials.connection_file().is_file()
    app.api_key.set('')
    app.llm = g.LLMSettings()
    app._restore_connection(restore_profile=True)
    assert app.llm.model == 'persisted-model' and app.llm.base_url == 'https://example.test/v1'
    assert app.api_key.get() == 'test-persisted-key' and app.api_key_is_saved
    app.open_settings()
    app.clear_saved_button.invoke()
    assert credentials.load_connection() is None and not app.api_key.get()
    assert not app.remember_connection.get()
    app.settings_window.destroy()


def test_saved_profile_does_not_override_map_from_active_config(app):
    import geoask_credentials as credentials
    saved_llm = app.llm.model_copy(update={'model': 'saved-model'})
    stale_map = g.MapSettings(
        tile_url='https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        attribution='© OpenStreetMap contributors',
        coordinate_system='wgs84')
    credentials.save_connection(saved_llm.model_dump(), stale_map.model_dump(),
                                app.routing_settings.model_dump(), 'saved-key')
    app._restore_connection(restore_profile=True)
    assert app.llm.model == 'saved-model'
    assert app.api_key.get() == 'saved-key'
    # The active YAML remains authoritative for map/routing settings.
    assert app.map_settings == app.settings.map
    assert app.routing_settings == app.settings.routing
    assert app.map_settings.tile_url == app.settings.map.tile_url


def test_save_failure_preserves_dialog_and_session(app, monkeypatch):
    import geoask_credentials as credentials
    previous = app.llm
    app.open_settings()
    monkeypatch.setattr(credentials, 'save_connection', MagicMock(side_effect=credentials.LocalSettingsError('无法保存接口设置')))
    app.apply_settings_button.invoke()
    assert app.settings_window.winfo_exists() and app.llm == previous
    assert '无法保存' in gui.messagebox.showerror.call_args.args[1]


def test_deselect_remember_uses_session_only(app):
    import geoask_credentials as credentials
    credentials.save_connection(app.llm.model_dump(), app.map_settings.model_dump(), app.routing_settings.model_dump(), 'old-key')
    app.open_settings()
    app.remember_connection.set(False)
    app.apply_settings_button.invoke()
    assert credentials.load_connection() is None and '仅在本次' in app.status.get()


@pytest.mark.parametrize('restored', [True, False], ids=['saved-key', 'explicit-input'])
def test_submit_preserves_key_priority(app, monkeypatch, restored):
    import geoask_credentials as credentials
    credentials.save_connection(app.llm.model_dump(), app.map_settings.model_dump(), app.routing_settings.model_dump(), 'saved-test-key')
    monkeypatch.setenv(app.llm.api_key_env, 'environment-test-key')
    app._restore_connection()
    if not restored:
        app.api_key.set('session-test-key')
        app.api_key_is_saved = False
    app.query.set('我想喝咖啡')
    app.submit()
    _, kind, request = app.jobs.get_nowait()
    assert kind == 'query'
    assert request.api_key == ('' if restored else 'session-test-key')
    assert (request.api_key or g.resolve_api_key(app.llm)) == ('environment-test-key' if restored else 'session-test-key')
    app._set_busy(False)


def test_saved_key_not_restored_into_different_config_provider(app):
    import geoask_credentials as credentials
    credentials.save_connection(app.llm.model_dump(), app.map_settings.model_dump(), app.routing_settings.model_dump(), 'saved-test-key')
    app.llm = app.llm.model_copy(update={'base_url': 'https://different.example/v1'})
    app._restore_connection()
    assert not app.api_key.get()
    assert app.llm.base_url == 'https://different.example/v1'


@pytest.mark.parametrize('same_provider', [True, False])
def test_switch_config_preserves_session_key_only_for_same_provider(app, same_provider):
    app.api_key.set('session-key-for-region')
    app.api_key_is_saved = False
    if not same_provider:
        app.llm = app.llm.model_copy(update={'base_url': 'https://other.example/v1'})
    app._load_config(g.DEFAULT_CONFIG)
    assert app.api_key.get() == ('session-key-for-region' if same_provider else '')
    assert not app.api_key_is_saved


def test_busy_freezes_preview_and_auto_route_without_changing_values(app):
    app.preview_only.set(True)
    app.auto_route.set(False)
    app._set_busy(True)
    assert app.preview.instate(['disabled']) and app.auto_route_toggle.instate(['disabled'])
    app.preview.invoke()
    app.auto_route_toggle.invoke()
    assert app.preview_only.get() and not app.auto_route.get()
    app._set_busy(False)
    assert not app.preview.instate(['disabled']) and not app.auto_route_toggle.instate(['disabled'])
    app.preview.invoke()
    app.auto_route_toggle.invoke()
    assert not app.preview_only.get() and app.auto_route.get()


def test_parking_event_shows_total_and_coordinates_for_unnamed_places(app):
    from geoask_parking import ParkingOptions
    value = result()
    value = replace(value, stops=[replace(value.stops[0], selected_index=0)])
    app._show_result(value)
    app.parking_enabled.set(True)
    rows = [dict(name='', category='parking', longitude=116.47001, latitude=39.92501, dist_m=100.)]
    options = ParkingOptions(value, 1000, rows, 25)
    app._handle_event(('parking', options))
    assert '25' in app.parking_message.get() and '最近 1 个' in app.parking_message.get()
    assert '116.47001' in app.parking_combo.cget('values')[0]
    assert str(app.parking_combo.cget('state')) == 'readonly'
    app.parking_enabled.set(False)


def test_reselect_error_retains_result_and_close_is_idempotent(app):
    value = result()
    app._show_result(value)
    app._set_busy(True)
    app._handle_event(("reselect_error", "无法读取目录"))
    assert app.result is value and not app.busy
    assert str(app.stop_combos[0].cget("state")) == "readonly"
    assert "已保留原行程" in app.status.get()
    app.close()
    app.close()


def test_worker_stamps_generation_on_progress_success_and_reselect_error(monkeypatch):
    value = result()
    engine = NS(close=MagicMock())
    def run(request, progress):
        progress("working")
        return value
    engine.run = run
    engine.reselect = MagicMock(side_effect=g.GeoaskError("换点失败"))
    monkeypatch.setattr(gui, "QueryEngine", lambda: engine)
    fake = NS(jobs=Queue(), events=Queue())
    fake.jobs.put((11, "query", value.request))
    fake.jobs.put((12, "reselect", (value, 0, 1)))
    fake.jobs.put(None)
    gui.GeoaskApp._work(fake)
    assert fake.events.get_nowait() == (11, "progress", "working")
    assert fake.events.get_nowait() == (11, "result", value)
    assert fake.events.get_nowait() == (12, "reselect_error", "换点失败")
    engine.close.assert_called_once()
