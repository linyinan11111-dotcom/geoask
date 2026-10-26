"""Verify real GUI + map tiles + real multi-stop road routing; no paid LLM calls."""
import json
import sys
import time
import tempfile
import traceback
import hashlib
from datetime import datetime, timezone
from unittest.mock import patch
from pathlib import Path
from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geoask as g
from geoask_gui import GeoaskApp
import geoask_credentials as credentials


def main():
    temporary = tempfile.TemporaryDirectory(prefix='geoask-gui-')
    region_dir = Path(temporary.name)
    # Saving settings in this probe must never overwrite the user's real profile.
    profile_patch = patch.object(credentials, 'connection_file', lambda: region_dir / 'connection.dat')
    profile_patch.start()
    app = GeoaskApp()
    output = g.ROOT / 'docs'
    report = {'ui_ticks': 0, 'verified_at': datetime.now(timezone.utc).isoformat(),
              'code_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in g.ROOT.glob('geoask*.py')}}
    deadline = time.monotonic() + 320
    phase = 'ambiguous'
    alternative = None
    original_parse_plan = g.parse_plan
    assert not app.parking_enabled.get()
    region_source = region_dir / 'guangzhou-test.geojson'
    region_source.write_text(json.dumps({'type':'FeatureCollection','features':[
        {'type':'Feature','properties':{'title':'广州塔', 'alt_name':'小蛮腰;Canton Tower'},
         'geometry':{'type':'Point','coordinates':[113.3245,23.1065]}}
    ]}, ensure_ascii=False), encoding='utf-8')
    imported_path = region_dir / 'guangzhou.yaml'

    def capture(window, path):
        window.deiconify()
        window.lift()
        window.update()
        # Windows may briefly return a black compositor surface after a window
        # is covered/minimized. Retry only capture; never accept a black artifact.
        for attempt in range(3):
            picture = ImageGrab.grab(window=window.winfo_id())
            if picture.convert('L').getextrema()[1] > 100:
                break
            time.sleep(.2)
            window.update()
        assert picture.width > 100 and picture.height > 100
        assert picture.convert('L').getextrema()[1] > 100, 'Black capture'
        picture.save(path)

    def tick():
        report['ui_ticks'] += 1
        if not app.closed:
            app.after(30, tick)

    def finish(error=None):
        g.parse_plan = original_parse_plan
        report['error' if error else 'passed'] = str(error) if error else True
        (output / 'gui-verification.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        app.close()

    def check():
        nonlocal phase, alternative
        try:
            assert time.monotonic() < deadline, 'Timed out: ' + phase
            if app.busy:
                app.after(150, check)
                return
            if phase == 'ambiguous':
                assert app.result and app.result.stops[0].candidates, app.notice.cget('text')
                stop = app.result.stops[0]
                assert stop.selected is None and '请选择具体地点' in app.status.get()
                assert app.route_button.cget('state') == 'disabled'
                if app.map_view.pending:
                    app.after(150, check)
                    return
                width, height = app.map_view.winfo_width(), app.map_view.winfo_height()
                positions = [app.map_view.screen_point(r['longitude'], r['latitude']) for r in stop.candidates]
                assert all(20 < x < width-20 and 20 < y < height-20 for x, y in positions)
                capture(app, output / 'gui-ambiguous.png')
                report['ambiguous_candidates_visible'] = {'shown': len(positions), 'zoom': app.map_view.zoom,
                    'total_name_matches': stop.candidates[0]['name_match_count'], 'explicit_selection_required': True}
                app._set_busy(True)
                app.events.put(('route', ()))  # Inject a handler failure, not a worker failure.
                app.events.put(('progress', '轮询已恢复'))
                phase = 'poll_recovery'
            elif phase == 'poll_recovery':
                assert not app.busy and app.status.get() == '轮询已恢复'
                assert app.search_button.cget('state') == 'normal'
                report['handler_exception_recovery_ok'] = True
                g.parse_plan = original_parse_plan
                app.api_key.set('')
                app.auto_route.set(True)
                app.start_trip_demo()
                phase = 'trip'
            elif phase == 'trip':
                assert app.result is not None, app.notice.cget('text')
                assert len(app.result.stops) == 2 and all(s.selected for s in app.result.stops)
                assert app.road_route is not None, app.route_summary.get()
                assert len(app.road_route.legs) == 2
                assert app.road_route.instruction_language == 'zh-CN'
                assert app.road_route.legs[0].instructions and not app.road_route.legs[0].instructions[0].startswith('Walk')
                if app.map_view.tiles_loaded < 4 or app.map_view.pending:
                    app.after(150, check)
                    return
                report.update({'stops': [s.selected['name'] for s in app.result.stops],
                               'route_m': app.road_route.distance_m, 'route_s': app.road_route.duration_s,
                               'real_map_tiles': app.map_view.tiles_loaded,
                               'route_geometry_points': len(app.road_route.coordinates)})
                capture(app, output / 'gui-route.png')
                report['route_language'] = {'provider': app.road_route.language,
                    'display': app.road_route.instruction_language, 'source': app.road_route.instruction_source,
                    'first_instruction': app.road_route.legs[0].instructions[0]}
                before = app.map_view.zoom
                app.map_view.zoom_at(1)
                assert app.map_view.zoom == before + 1
                app.map_view.zoom_at(-1)
                assert app.map_view.zoom == before
                report['zoom_in_and_out_ok'] = True
                app.show_candidates(1)
                assert app.table.get_children()
                assert app.result.stops[1].candidates[0]['category'] == 'fitness_centre'
                capture(app, output / 'gui-candidates.png')
                first = app.result.stops[0]
                alternative = next((i for i in range(len(first.candidates)) if i != first.selected_index), first.selected_index)
                app.reselect_stop(0, alternative)
                phase = 'reselect'
            elif phase == 'reselect':
                assert app.result.stops[0].selected_index == alternative
                assert app.road_route is not None, app.route_summary.get()
                assert app.result.stops[1].center == (app.result.stops[0].selected['longitude'], app.result.stops[0].selected['latitude'])
                report['reselect_and_reroute_ok'] = True
                app.auto_route.set(False)
                app.preview_only.set(True)
                app.start_trip_demo()
                phase = 'preview'
            elif phase == 'preview':
                assert app.result.request.dry_run and app.result.stops[1].center is None
                assert app.road_route is None and app.route_button.cget('state') == 'disabled'
                assert 'ST_Distance_Spheroid' in app.sql_view.get('1.0','end')
                report['preview_ok'] = True
                app.geometry('1120x800')
                app.main_tabs.select(0)
                phase = 'compact'
            elif phase == 'compact':
                assert app.map_view.winfo_height() > 200
                assert app.status_label.winfo_rooty()+app.status_label.winfo_height() < app.winfo_rooty()+app.winfo_height()
                report['compact_layout_ok'] = True
                app.longitude.set('116.452')
                app.latitude.set('39.913')
                app.center_map()
                assert app.map_view.center[0] == 116.452
                app.map_origin(116.46,39.92)
                assert float(app.longitude.get()) == 116.46
                report['manual_and_map_origin_ok'] = True
                app.preview_only.set(False)
                app.auto_route.set(True)
                app.geometry('1420x940')
                app.api_key.set('gui-regression-test-not-a-real-key')
                g.parse_plan = lambda *a, **kw: g.GeoPlan(stops=[g.GeoIntent(
                    intent='演唱会', modifiers=[], place_name='鸟巢')], travel_mode='walking')
                app.query.set('我想去鸟巢看演唱会')
                app.submit()
                phase = 'named'
            elif phase == 'named':
                assert app.result is not None, app.notice.cget('text')
                stop = app.result.stops[0]
                assert len(app.result.stops) == 1 and stop.selected['name'] == '国家体育场'
                assert stop.selected['dist_m'] > app.result.request.radius
                assert '不受附近半径限制' in app.explanation.get('1.0', 'end')
                assert '相似度' not in app.explanation.get('1.0', 'end')
                assert app.road_route is not None, app.route_summary.get()
                assert len(app.road_route.legs) == 1
                if app.map_view.pending:
                    app.after(150, check)
                    return
                capture(app, output / 'gui-named-place.png')
                report['named_destination'] = {
                    'query': app.result.request.query, 'llm': 'fixed regression response; no paid API call',
                    'resolved_name': stop.selected['name'], 'coordinates': [stop.selected['longitude'], stop.selected['latitude']],
                    'straight_distance_m': stop.selected['dist_m'], 'nearby_radius_m': app.result.request.radius,
                    'real_road_distance_m': app.road_route.distance_m, 'route_s': app.road_route.duration_s,
                    'geometry_points': len(app.road_route.coordinates),
                }
                app.auto_route.set(False)
                app.parking_toggle.invoke()
                phase = 'parking_found'
            elif phase == 'parking_found':
                assert app.parking_options and app.parking_options.candidates, app.route_summary.get()
                assert app.parking_options.destination['name'] == '国家体育场'
                assert app.parking_index is None and app.road_route is None
                assert all(r['dist_m'] <= 1000 and r['category'] == 'parking' for r in app.parking_options.candidates)
                app.parking_combo.current(0)
                app.select_parking()
                assert app.parking_index == 0
                app.plan_route()
                phase = 'parking_route'
            elif phase == 'parking_route':
                assert app.road_route and app.road_route.travel_mode == 'driving', app.route_summary.get()
                assert app.route_parking and app.route_parking['category'] == 'parking'
                assert app.result.stops[-1].selected['name'] == '国家体育场'
                assert '停车场' in app.route_summary.get()
                assert '不是步行路线' in app.navigation.get('1.0','end')
                assert any(m.get('label')=='P' for m in app.map_view.markers)
                if app.map_view.pending:
                    app.after(150, check)
                    return
                capture(app, output / 'gui-parking-route.png')
                report['parking_navigation'] = {'source':'real bundled Beijing OSM traffic layers',
                    'parking':app.route_parking, 'destination':app.result.stops[-1].selected['name'],
                    'mode':app.road_route.travel_mode, 'road_m':app.road_route.distance_m,
                    'road_s':app.road_route.duration_s, 'geometry_points':len(app.road_route.coordinates)}
                app.parking_radius.set('1')
                app.find_parking()
                phase = 'parking_missing'
            elif phase == 'parking_missing':
                assert app.parking_options and not app.parking_options.candidates
                assert app.road_route is None and app.route_parking is None
                assert app.result.stops[-1].selected['name'] == '国家体育场'
                app.parking_toggle.invoke()
                assert not app.parking_enabled.get() and app.parking_options is None
                assert str(app.parking_combo.cget('state')) == 'disabled'
                report['parking_empty_and_disable_ok'] = True
                with patch('geoask_gui.filedialog.askopenfilename', return_value=str(region_source)):
                    app.import_button.invoke()
                phase = 'import_dialog'
            elif phase == 'import_dialog':
                dialog = app.import_window
                assert dialog and dialog.winfo_exists()
                assert app.import_apply_button.winfo_ismapped()
                assert app.import_apply_button.winfo_height() >= 35
                assert app.import_apply_button.winfo_rooty()+app.import_apply_button.winfo_height() < dialog.winfo_rooty()+dialog.winfo_height()
                app.import_fields['name'].set('title')
                app.import_fields['category'].set('')
                capture(dialog, output / 'gui-import.png')
                with patch('geoask_gui.filedialog.asksaveasfilename', return_value=str(imported_path)):
                    app.import_apply_button.invoke()
                phase = 'imported_region'
            elif phase == 'imported_region':
                assert app.config_path == imported_path and imported_path.exists(), app.status.get()
                assert app.result is None and app.road_route is None
                assert not app.stop_combos
                assert app.settings.place_catalog_path is None and not app.settings.place_aliases
                assert float(app.longitude.get()) == 113.3245
                assert 'gui-regression-test-not-a-real-key' not in imported_path.read_text(encoding='utf-8')
                g.parse_plan = lambda *a, **kw: g.GeoPlan(stops=[g.GeoIntent(
                    intent='观光', modifiers=[], place_name='小蛮腰')], travel_mode='walking')
                app.query.set('我想去小蛮腰观光')
                app.submit()
                phase = 'regional_query'
            elif phase == 'regional_query':
                assert app.result and app.result.stops[0].selected, app.notice.cget('text')
                stop = app.result.stops[0]
                assert stop.selected['name'] == '广州塔' and stop.selected['name_rank'] == 0
                assert stop.selected['category'] == 'unknown'
                assert app.route_button.cget('state') == 'normal'
                assert app.result.request.config_path == imported_path
                assert app.road_route is None
                if app.map_view.pending:
                    app.after(150, check)
                    return
                capture(app, output / 'gui-region.png')
                report['regional_import'] = {'source':'synthetic Guangzhou fixture; not a published POI dataset',
                    'alias':'小蛮腰', 'resolved_name':stop.selected['name'], 'coordinates':[stop.selected['longitude'],stop.selected['latitude']],
                    'without_category':True, 'old_trip_cleared':True, 'beijing_catalog_cleared':True,
                    'llm':'fixed regression response; no paid API call'}
                g.parse_plan = original_parse_plan
                app.api_key.set('')
                app.config_path = output / 'does-not-exist.yaml'
                app.start_demo()
                phase = 'error'
            elif phase == 'error':
                assert app.result is None and '无法读取配置' in app.notice.cget('text')
                assert app.search_button.cget('state') == 'normal'
                assert not app.stop_combos
                report['error_recovery_ok'] = True
                app.config_path = g.ROOT / 'config.yaml'
                app.open_settings()
                phase = 'settings'
            elif phase == 'settings':
                window = app.settings_window
                assert window and window.winfo_exists()
                assert app.apply_settings_button.winfo_ismapped()
                assert app.apply_settings_button.winfo_rooty()+app.apply_settings_button.winfo_height() < window.winfo_rooty()+window.winfo_height()
                capture(window, output / 'gui-settings.png')
                report['settings_dialog_ok'] = True
                tabs = next(child for child in window.winfo_children() if child.winfo_class() == 'TNotebook')
                panel = tabs.winfo_children()[0]
                entries = [c for c in panel.winfo_children() if c.winfo_class() == 'TEntry']
                entries[-1].insert(0, 'unsaved-session-key')
                tabs.select(1)
                phase = 'map_settings'
            elif phase == 'map_settings':
                capture(app.settings_window, output / 'gui-map-settings.png')
                report['map_settings_tab_ok'] = True
                tabs = next(c for c in app.settings_window.winfo_children() if c.winfo_class() == 'TNotebook')
                panel = tabs.winfo_children()[1]
                buttons = [c for c in panel.winfo_children() if c.winfo_class() == 'Button']
                buttons[-1].invoke()
                assert app.api_key.get() == 'unsaved-session-key'
                assert not app.settings_window.winfo_exists()
                report['apply_preserves_other_tab_key_ok'] = True
                assert credentials.load_connection()['api_key'] == 'unsaved-session-key'
                app.api_key.set('')
                app._restore_connection(restore_profile=True)
                assert app.api_key.get() == 'unsaved-session-key' and app.api_key_is_saved
                app.open_settings()
                app.clear_saved_button.invoke()
                assert credentials.load_connection() is None and not app.api_key.get()
                app.settings_window.destroy()
                report['local_connection_save_restore_clear_ok'] = True
                finish()
                return
            app.after(200, check)
        except Exception as exc:
            report['failed_phase'] = phase
            report['traceback'] = traceback.format_exc()
            finish(exc)

    app.after(30,tick)
    def start():
        try:
            capture(app, output / 'gui.png')
            app.api_key.set('gui-regression-test-not-a-real-key')
            app.auto_route.set(False)
            g.parse_plan = lambda *a, **kw: g.GeoPlan(stops=[g.GeoIntent(
                intent='咖啡', modifiers=[], place_name='星巴克')], travel_mode='walking')
            app.query.set('去星巴克')
            app.submit()
            # capture() pumps Tk events. Start result checks only after the
            # first submission, so a slow compositor cannot close the app early.
            app.after(350, check)
        except Exception as exc:
            report['failed_phase'] = 'startup'
            report['traceback'] = traceback.format_exc()
            finish(exc)

    app.after(350, start)
    try:
        app.mainloop()
    finally:
        g.parse_plan = original_parse_plan
        app.close()
        profile_patch.stop()
        temporary.cleanup()
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if not report.get('passed'):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
