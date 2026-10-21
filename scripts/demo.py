"""Key-free demo: mock only intent parsing; run real embeddings + real OSM search."""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import geoask


def main():
    print('离线演示：LLM 意图响应使用测试桩，嵌入和 OSM 空间查询均为真实执行。')
    with patch.object(geoask, 'parse_intent', return_value=geoask.GeoIntent(intent='咖啡', modifiers=[])):
        geoask.cli.main(args=['我想喝咖啡', '--near', '116.46,39.92', '--explain'], standalone_mode=False)


if __name__ == '__main__':
    main()
