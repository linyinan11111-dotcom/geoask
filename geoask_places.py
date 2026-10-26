"""Resolve explicit destinations from real data; never embed a proper name as a category."""
from __future__ import annotations

import re
import json
import unicodedata

import geoask as g


def name_key(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).casefold()


def prepare_places(con, settings: g.Settings):
    """Index names/aliases from the active data, plus an optional regional catalogue."""
    if con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'named_places'").fetchone()[0]:
        return
    con.execute("CREATE TEMP TABLE named_places (name VARCHAR, category VARCHAR, aliases VARCHAR[], geometry GEOMETRY)")
    try:
        # Connections built by load_data retain named places even without categories.
        has_source = con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'source_places'").fetchone()[0]
        if has_source:
            con.execute("INSERT INTO named_places SELECT name,category,aliases,geometry FROM source_places")
        else:
            con.execute("INSERT INTO named_places SELECT name,category,[]::VARCHAR[],geometry FROM poi")
        if settings.place_catalog_path is not None:
            if not settings.place_catalog_path.is_file():
                raise g.GeoaskError(f"地名目录不存在：{settings.place_catalog_path}")
            sql, params = g.source_select(con, settings.place_catalog_path, settings.place_catalog_layer,
                                          settings.place_catalog_field_mapping, settings.place_catalog_source_crs)
            con.execute("INSERT INTO named_places " + sql, params)
        invalid = con.execute("""SELECT count(*) FROM named_places WHERE (
            isfinite(ST_X(geometry)) AND isfinite(ST_Y(geometry))
            AND ST_X(geometry) BETWEEN -180 AND 180 AND ST_Y(geometry) BETWEEN -90 AND 90) IS NOT TRUE""").fetchone()[0]
        if invalid:
            raise g.GeoaskError("地名目录含无效 WGS84 坐标。")
        con.execute("ALTER TABLE named_places ADD COLUMN name_key VARCHAR")
        con.execute("ALTER TABLE named_places ADD COLUMN alias_keys VARCHAR[]")
        # The same Python Unicode function normalizes both dataset and query.
        # RE2 \s/lower have different Unicode behavior from Python.
        values = con.execute("SELECT rowid, name, aliases FROM named_places").fetchall()
        keys = [{"id": row[0], "key": name_key(row[1]),
                 "alias_keys": list(dict.fromkeys(name_key(a) for a in (row[2] or []) if name_key(a)))}
                for row in values]
        # Transfer a single JSON value rather than Python nested arrays (which
        # trigger costly optional dataframe imports in DuckDB's Python bridge).
        con.execute("""CREATE TEMP TABLE geoask_normalized_names AS
            SELECT item.id AS id, item.key AS key, item.alias_keys AS alias_keys
            FROM unnest(from_json(?, '[{"id":"BIGINT","key":"VARCHAR","alias_keys":["VARCHAR"]}]')) AS t(item)""",
                    [json.dumps(keys, ensure_ascii=False)])
        try:
            con.execute("""UPDATE named_places SET
                name_key = normalized.key, alias_keys = normalized.alias_keys
                FROM geoask_normalized_names normalized WHERE named_places.rowid = normalized.id""")
        finally:
            con.execute("DROP TABLE geoask_normalized_names")
    except g.duckdb.Error as exc:
        con.execute("DROP TABLE named_places")
        g.get_logger().exception("Regional name catalogue loading failed")
        raise g.GeoaskError("地名目录加载失败，请核对目录文件、源坐标系及几何字段映射。") from exc
    except Exception:
        con.execute("DROP TABLE named_places")
        raise


def build_place_query(place_name, center, limit, settings, candidates=()):
    g.validate_location(*center)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000:
        raise ValueError("结果数量必须在 [1,1000] 内。")
    if not name_key(place_name):
        raise ValueError("具体地点名称不能为空。")
    aliases = {name_key(k): v for k, v in settings.place_aliases.items() if name_key(k) and name_key(v)}
    canonical = aliases.get(name_key(place_name), place_name)
    key = name_key(canonical)
    candidate_keys = list(dict.fromkeys(name_key(aliases.get(name_key(c), c)) for c in candidates if name_key(c)))
    candidate_keys = [c for c in candidate_keys if c]
    # Explicit names/data aliases rank first. LLM aliases are exact-only and must
    # be confirmed by the user. Original-name substring matching is the last resort.
    sql = """WITH lookup AS (
    SELECT CASE WHEN EXISTS (SELECT 1 FROM named_places WHERE name_key = ? OR list_contains(alias_keys, ?))
           THEN ? ELSE ? END AS exact_key
), ranked AS (
    SELECT named_places.*, CASE
        WHEN name_key = exact_key OR list_contains(alias_keys, exact_key) THEN 0
        WHEN list_contains(?::VARCHAR[], name_key) OR list_has_any(alias_keys, ?::VARCHAR[]) THEN 1
        WHEN length(?) >= 2 AND (contains(name_key, ?) OR
            len(list_filter(alias_keys, x -> contains(x, ?))) > 0) THEN 2
        ELSE 3 END AS name_rank
    FROM named_places CROSS JOIN lookup
), matches AS (
    SELECT * FROM ranked WHERE name_rank < 3
), preferred AS (
    SELECT * FROM matches WHERE name_rank = (SELECT min(name_rank) FROM matches)
), distances AS (
    SELECT name, category, name_rank, ST_X(geometry) AS longitude, ST_Y(geometry) AS latitude,
           ST_Distance_Spheroid(geometry, ST_Point(?, ?)) AS dist_m
    FROM preferred
), unique_places AS (
    SELECT name, coalesce(min(category), 'unknown') AS category, min(name_rank) AS name_rank,
           dist_m, longitude, latitude FROM distances
    GROUP BY name, dist_m, longitude, latitude
)
SELECT *, count(*) OVER () AS name_match_count FROM unique_places
ORDER BY dist_m, name, category LIMIT ?"""
    return sql, [key, key, key, name_key(place_name), candidate_keys, candidate_keys, key, key, key, *center, limit]


def search_place(con, place_name, center, limit, settings, candidates=()):
    prepare_places(con, settings)
    sql, params = build_place_query(place_name, center, limit, settings, candidates)
    cursor = con.execute(sql, params)
    rows = [dict(zip([d[0] for d in cursor.description], r)) for r in cursor.fetchall()]
    return rows
