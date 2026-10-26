"""Online integration probe: real local POIs + real pedestrian route; no LLM key."""
import json
import hashlib
from datetime import datetime, timezone
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geoask as g
from geoask_service import QueryEngine, QueryRequest, route_points
from geoask_routing import plan_road_route
from geoask_runtime import atomic_output


def main():
    engine = QueryEngine()
    try:
        result = engine.run(QueryRequest("先喝咖啡然后健身", (116.46,39.92), 1000,10,
                                         demo=True, planning=True, demo_trip=True), print)
        print([(stop.intent.intent, stop.selected) for stop in result.stops], flush=True)
        route = plan_road_route(route_points(result), result.travel_mode, g.RoutingSettings())
        report = {"verified_at": datetime.now(timezone.utc).isoformat(),
                  "code_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in g.ROOT.glob('geoask*.py')},
                  "llm": "fixed demo intent", "poi_data": "real bundled OSM",
                  "stops": [dict(intent=s.intent.intent, selected=s.selected) for s in result.stops],
                  "route_provider": route.provider, "mode": route.travel_mode, "distance_m": route.distance_m,
                  "duration_s": route.duration_s, "legs": len(route.legs), "geometry_points": len(route.coordinates)}
        with atomic_output(g.ROOT / "docs" / "trip-verification.json") as output:
            output.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=True, indent=2))
    finally:
        engine.close()


if __name__ == "__main__":
    main()
