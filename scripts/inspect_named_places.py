"""Inspect real source names before selecting the named-destination catalogue."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geoask import ROOT, connect_spatial, resolve_layer


def main():
    path = ROOT / '.downloads/source/beijing.gpkg'
    with connect_spatial() as con:
        for requested in ('gis_osm_pois_free', 'gis_osm_pois_a_free', 'gis_osm_transport_free'):
            layer = resolve_layer(con, path, requested)
            source = [str(path), layer]
            print(layer)
            print(con.execute("SELECT count(*),count(nullif(trim(name), '')) FROM ST_Read(?,layer=?)", source).fetchall())
            print(con.execute("""SELECT osm_id,name,fclass,ST_X(ST_PointOnSurface(geom)),ST_Y(ST_PointOnSurface(geom))
                FROM ST_Read(?,layer=?) WHERE name LIKE '%鸟巢%' OR name LIKE '%国家体育场%'""", source).fetchall())
            print(con.execute("""SELECT fclass,count(*) FROM ST_Read(?,layer=?)
                WHERE nullif(trim(name),'') IS NOT NULL GROUP BY fclass ORDER BY count(*) DESC LIMIT 30""", source).fetchall())


if __name__ == '__main__':
    main()
