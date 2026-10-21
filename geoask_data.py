"""Validate a new regional file and save an independent reusable configuration."""
from pathlib import Path
import json
import os
import tempfile

import yaml

import geoask as g


def import_region(source: Path, destination: Path, base: g.Settings, *, layer=None,
                  name="name", category="fclass", geometry="geom", aliases=(), source_crs="EPSG:4326"):
    source, destination = source.resolve(), destination.resolve()
    if destination == source or destination.suffix.lower() not in ('.yaml', '.yml'):
        raise g.GeoaskError("请把地区配置另存为 YAML 文件，不能覆盖数据文件。")
    if destination.exists():
        raise g.GeoaskError("该配置文件已存在，请选择新的文件名。")
    settings = base.model_copy(deep=True)
    settings.geojson_path, settings.layer = source, layer or None
    settings.field_mapping = g.FieldMapping(name=name, category=category or None, geometry=geometry, aliases=list(aliases))
    settings.source_crs = source_crs
    settings.place_catalog_path = settings.place_catalog_for = None
    settings.place_aliases = {}
    settings.place_catalog_layer = 'places'
    settings.place_catalog_field_mapping = g.FieldMapping()
    settings.place_catalog_source_crs = 'EPSG:4326'
    # Validate data/CRS/mappings before creating any saved config; no model/API calls.
    with g.load_data(settings) as con:
        count = con.execute('SELECT count(*) FROM source_places').fetchone()[0]
        named = con.execute("SELECT count(*) FROM source_places WHERE nullif(trim(name),'') IS NOT NULL OR len(aliases)>0").fetchone()[0]
        if not named:
            raise g.GeoaskError("数据中没有名称或别名，请核对名称字段。")
        point = con.execute('SELECT ST_X(geometry), ST_Y(geometry) FROM source_places LIMIT 1').fetchone()
        settings.default_location = point
    settings.cache_dir = (base.cache_dir if base.cache_dir.is_absolute() else g.ROOT / base.cache_dir).resolve()
    payload = json.loads(settings.model_dump_json())
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        # A complete temporary file plus hard-link avoids replacing an existing config.
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=destination.parent,
                                         suffix='.yaml', delete=False) as output:
            temporary = Path(output.name)
            yaml.safe_dump(payload, output, allow_unicode=True, sort_keys=False)
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return destination, count, named
